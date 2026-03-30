---
name: play-spring-transformer-compact
description: Compact decision-tree skill for Play→Spring file transformation.
---

# Transformer: Play → Spring

## Layer Detection (path under app/)
| Layer | Path rule |
|-------|-----------|
| controller | `/controllers/` |
| service | `/service/` or `/services/` |
| model | `/models/` or `*Model.java` |
| manager | `/db/` |
| repository | `/repositories/` or `/dao/` |
| other | everything else |

## Transform Command
```bash
java -jar dev-toolkit-1.0.0.jar migrate-app --path-prefix <prefix> [--batch-size N]
```
- Runs from Play repo root
- Skips already-migrated files (idempotent)
- Output: `migrate-app done: N files, M errors, R remaining`

## AST Rewrite Rules (dev-toolkit JAR)
- `@Inject` → `@Autowired` (optional fields → `required=false`)
- `@Singleton` → `@Component` (skip if @RestController/@Service exists)
- `play.mvc.Result` → `ResponseEntity`
- `play.mvc.Controller` → `@RestController`
- `play.Logger` → SLF4J `LoggerFactory`
- `javax.inject.Provider<T>` → `ObjectProvider<T>` + `.get()` → `.getObject()`
- `F.Promise` / `CompletionStage` → keep or simplify

## Rules
- Never modify Play source files
- Preserve business logic verbatim
- Edit ONLY Spring project under src/main/java/
