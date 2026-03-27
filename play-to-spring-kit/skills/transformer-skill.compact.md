# Transformer: CLI Commands & AST Rules

## Goal
Transform Play Framework classes into Spring Boot via **dev-toolkit CLI**. All commands are CLI-only. Layer scope matches `LayerDetector` and `source_inventory.by_layer` in `migration-status.json`.

## Commands (run from Play repo)

### Migrate entire app
```bash
java -jar dev-toolkit-1.0.0.jar migrate-app
```
Default: source = `.`, target = `../spring-<basename>`.

### Migrate a folder slice (path-prefix)
```bash
java -jar dev-toolkit-1.0.0.jar migrate-app --path-prefix <rel-to-app>
```

### Migrate a single layer
```bash
java -jar dev-toolkit-1.0.0.jar migrate-app --layer model
```
Valid: `model`, `repository`, `manager`, `service`, `controller`, `other`.

### Batch mode
```bash
java -jar dev-toolkit-1.0.0.jar migrate-app --layer service --batch-size 15
```
Repeat until `remaining = 0`. CLI skips already-migrated files (idempotent).

### Single file transform (retry)
```bash
java -jar dev-toolkit-1.0.0.jar transform --input <play-file> --output <spring-file> [--layer controller]
```

### CLI Output
```
migrate-app done: N files, M errors, R remaining
```
Exit 0 = all ok. Exit 1 = partial (still proceed to validate).

## Layer Detection (`LayerDetector`)
| Path rule | Layer |
|-----------|-------|
| `/controllers/` | CONTROLLER |
| `/service/` or `/services/` | SERVICE |
| `/models/` or `*Model.java` | MODEL |
| `/db/` | MANAGER |
| `/repositories/` or `/dao/` | REPOSITORY |
| everything else | OTHER |

## Core AST Rewrites (all layers)
- `@Inject` → `@Autowired` (optional fields: `@Autowired(required=false)`, optional ctor params: `Optional<T>`)
- `@Singleton` → `@Component` / `@Service` / `@RestController` (per layer)
- Play `Logger.ALogger` / `Log.getLogger` → SLF4J `LoggerFactory.getLogger`
- `Json.toJson` → `objectMapper.valueToTree`
- `Json.fromJson` → `objectMapper.convertValue`
- `Json.parse` → `objectMapper.readTree`
- `Json.newObject` → `objectMapper.createObjectNode`
- `WSClient` → `RestTemplate` + `@PreDestroy`

## ExtendedFixups (post-transform, deterministic)
- `javax.inject.Provider<T>` → `ObjectProvider<T>` + import
- `provider.get()` → `provider.getObject()`
- `@Singleton` → `@Component` (if no Spring stereotype present)
- `javax.inject.*` → `jakarta.inject.*`
- `javax.annotation.*` → `jakarta.annotation.*`
- Neo4j driver v1 → v5 imports/types
- `play.mvc.Http.Request` → `HttpServletRequest`
- `@Autowired(required=false)` on ctor params → `Optional<T>`

## Controller-Specific
- Remove `extends Controller`
- `Http.Request` → `@RequestBody JsonNode body` + optional `HttpServletRequest`
- `Result` → `ResponseEntity<JsonNode>`
- `CompletableFuture<Result>` → `CompletableFuture<ResponseEntity<JsonNode>>`
- `ok()` / `badRequest()` / `created()` → `ResponseEntity.ok()` / etc.
- `HttpExecution.fromThread(e)` → `e`
- Remove `@BodyParser` annotations

## Manual Fallback (when CLI fails for a file)
1. Read Play source
2. Apply Play→Spring mappings above
3. Write to `../spring-<basename>/src/main/java/`
4. Preserve business logic verbatim

## Rules
- **Never modify Play source files** — only Spring project
- Preserve business logic and query logic verbatim
- For `@Singleton` managers (MongoManager, GraphManager): `@Component`, constructor injection, `@PreDestroy`

## migration-status.json integration
- `layers.<layer>.files_migrated` / `files_failed` — updated from CLI output
- `source_inventory.by_layer` uses same `LayerDetector` rules for Play→Spring comparison
- After all layers, **builder Step 3** uses inventory vs Spring counts for verification

## Agent Verification
- Running `migrate-app` proves dev-toolkit ran, NOT that this skill was read
- Orchestrator should Read this file before any transform steps
- For compile-fix and `migration-status.json` validation, see **builder skill**
