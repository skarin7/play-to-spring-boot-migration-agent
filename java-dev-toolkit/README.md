# Java Dev Toolkit

## 🏗️ Build Instructions

To build the project, simply run:

```sh
mvn clean package
```

This will generate the executable JAR file in the `target/` directory.

## Quick Start

```bash
# Show all available commands
java -jar dev-toolkit-1.0.0.jar --help

# Get help for a specific command
java -jar dev-toolkit-1.0.0.jar split --help
```

## Method Introspector

The `MethodIntrospector` tool allows you to extract metadata about all methods in a given Java class file. It outputs a JSON file containing each method's name, return type, and number of lines (including static, private, public, etc.).

### Usage

```bash
java -jar dev-toolkit-1.0.0.jar introspect <path-to-java-file>
```

**Example:**
```bash
java -jar dev-toolkit-1.0.0.jar introspect src/data/PublishCommonService.java
```

This will produce a file named `PublishCommonService_methods.json` in the current directory.

### Output Format

```
{
  "moveLessFolderFromSrcToDest": {
    "return_type": "boolean",
    "num_lines": 61
  },
  ...
}
```

- Each key is a method name.
- `return_type` is the method's return type as a string.
- `num_lines` is the number of lines in the method (including signature and body).

---

## migrate-app (Play → Spring)

Walks a Play Framework repo’s `app/` tree and writes transformed Java files into a Spring-style project under `<target>/src/main/java`, preserving package paths relative to `app/`. Layer (controller, service, repository, etc.) is inferred from path segments (see `LayerDetector`).

### Usage

```bash
java -jar dev-toolkit-1.0.0.jar migrate-app [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--source` | Root of the Play repo (default: current directory `.`). Must contain an `app/` directory. |
| `--target` | Root of the Spring repo (default: `../spring-<basename of source>` or `spring_repo:` from `workspace.yaml` if present). |
| `--layer` | Process only one layer: `model`, `repository`, `manager`, `service`, `controller`, or `other`. Omit to process all. |
| `--batch-size` | Max number of files to transform this run; remaining files are reported as “remaining”. Default: no limit (`-1`). Files that already exist under the target are skipped. |
| `--report` | Write a JSON array of per-file transform results to this path. |
| `--dry-run` | Print what would be transformed; do not write files. |

### Examples

```bash
# From the Play repo root (expects ./app)
java -jar dev-toolkit-1.0.0.jar migrate-app

# Explicit Play and Spring roots
java -jar dev-toolkit-1.0.0.jar migrate-app --source /path/to/play-project --target /path/to/spring-project

# Preview only
java -jar dev-toolkit-1.0.0.jar migrate-app --source . --dry-run

# Only controllers, first 10 new files, with a report
java -jar dev-toolkit-1.0.0.jar migrate-app --layer controller --batch-size 10 --report ./migrate-report.json
```

### Single-file transform

For one file instead of the whole tree:

```bash
java -jar dev-toolkit-1.0.0.jar transform --input app/com/example/Foo.java --output /path/to/spring/src/main/java/com/example/Foo.java [--layer controller|service|...] [--report report.json]
```

Layer defaults to auto-detection from the input path when `--layer` is omitted.

### Scope and limitations

`migrate-app` / `transform` **do not** create a Spring Boot project from scratch. You still need a target repo with a build (`pom.xml` / Gradle) and dependencies.

The toolkit applies **AST-level** updates on each file, including:

- Spring stereotypes by folder layer (`@RestController`, `@Service`, `@Component`, `@Repository`)
- `javax.inject.Inject` / `com.google.inject.Inject` → Spring `@Autowired`: marker `@Inject` → `@Autowired`; **`@Inject(optional = true)`** on a **field or method** → **`@Autowired(required = false)`** (Spring honors `required` there). On a **constructor parameter**, **`@Inject(optional = true)`** → **`Optional<Dependency>`** with `@Autowired` on the constructor (Spring’s documented pattern for optional constructor injection). Guice does not allow `optional` on the constructor itself; if seen, it is mapped to `@Autowired` and a warning is recorded.
- **Post-pass:** `@Autowired(required = false)` on a **constructor parameter** (often from bad migrations) is rewritten to **`Optional<…>`** — Spring does not treat `required=false` per constructor argument the same way as for fields/setters.
- Play `Logger.ALogger` / `Log.getLogger(Class)` → SLF4J `LoggerFactory.getLogger(Class)`
- **All layers:** `play.libs.Json` → Jackson on an `@Autowired ObjectMapper objectMapper` field when needed: `Json.newObject` / `newArray` → `createObjectNode` / `createArrayNode`, `fromJson` / `toJson` / `parse` → `convertValue` / `valueToTree` / `readTree` (then **`SpringCompileFixups`** rewrites `objectMapper.readTree` → `JacksonJson.readTree`); unused `import play.libs.Json` is dropped.
- **All layers:** `play.libs.ws.WSClient` / `WSResponse` → `RestTemplate` / `ResponseEntity<String>`; common `ws.url(...).setHeader/post/get...toCompletableFuture()[.get()]` and `thenApply(WSResponse::asJson)` patterns → `RestTemplate` + `HttpHeaders`/`HttpEntity` + `JacksonJson.readTree` where needed; `play.inject.ApplicationLifecycle#addStopHook` → `@PreDestroy` method and constructor cleanup. **`migrate-app`** writes `JacksonJson.java` under `<base>.utils` when any migrated file needs it. Remaining Play-WS edge cases may need manual fixes.
- **Post-pass (`SpringCompileFixups`):** Spring 6 `HttpStatusCode` phrase handling, Neo4j `ServerAddressResolver` `List`→`Set`, servlet header access (`getHeaders().get`→`Optional.ofNullable(getHeader)`), `getErrorResult(UNAUTHORIZED)`→`HttpStatus.UNAUTHORIZED.value()`, controller `CompletableFuture.supplyAsync` generics, `ResponseEntity.ok("…")` for `JsonNode` returns, optional-dependency constructor fix (`@Autowired(required=false)` on ctor param → `Optional<T>`).
- **Controllers only:** drop `extends Controller`, remove `@BodyParser`, replace `HttpExecution.fromThread(executor)` with `executor`; rewrite `Http.Request` (JSON body + optional `javax.servlet.http.HttpServletRequest`), `Result` / `CompletableFuture<Result>` → `ResponseEntity<JsonNode>`, and common Play response helpers (`ok`, `created`, `badRequest`, …) to `ResponseEntity`; strip other obsolete `play.*` imports. Spring Boot 3 may need `jakarta.servlet` instead of `javax.servlet`.

You must still add **request mappings** (`@GetMapping`, `@PostMapping`, …), migrate any remaining Play request APIs (e.g. headers) to servlet/Spring types, and align service method signatures with callers. For Play `conf/routes`, a companion script in consuming repos can emit mapping hints, e.g. `python3 scripts/routes_to_spring_map.py conf/routes` in **cms-content-service**.

---

## Test Prompt Generation (WIP - don't use)

The toolkit includes a `TestPromptGenerator` that creates structured prompts for generating JUnit tests with LLMs. This tool extracts methods from Java classes and generates comprehensive prompts that can be used with ChatGPT, Gemini, or other LLMs.

### Usage

```bash
java -jar dev-toolkit-1.0.0.jar generate-prompts --package <directory> [--class <className>] [--output <fileName>]
```

**Parameters:**
- `--package`: Path to the package (directory) to search (required)
- `--class`: Name of the Java class (without .java) - optional
- `--output`: Output file name - optional

**Examples:**
```bash
# Generate prompts for specific class
java -jar dev-toolkit-1.0.0.jar generate-prompts --package src/main/java --class MyClass
```
```
I have a JSON object containing method names and their metadata. I need you to categorize these methods into logical service classes following these strict guidelines:

**CRITICAL VALIDATION REQUIREMENTS:**
- Every method from the input JSON must appear in exactly one service class array
- No method should be duplicated across multiple classes
- No method should be omitted from the output
- After categorization, verify that the total count of methods in all classes equals the original input count

**CATEGORIZATION GUIDELINES:**

1. **Functional Cohesion**: Group methods that perform related business functions together
2. **Data Cohesion**: Group methods that operate on the same data structures or models
3. **Reasonable Class Size**: Each class should have a maximum of 2000 lines of code (estimate based on method line counts)
4. **Clear Responsibilities**: Each class should have a single, well-defined purpose
5. **Naming Convention**: Use descriptive service class names ending with "Service", "Manager", or similar
6. **Logical Organization**:
   - Group related CRUD operations together
   - Separate utility/helper methods from business logic
   - Keep configuration and settings methods together
   - Separate query/retrieval methods from modification methods

**COMMON SERVICE PATTERNS TO CONSIDER:**
- LifecycleService (create, update, delete operations)
- QueryService (read/retrieval operations)
- ValidationService (validation and utility functions)
- RenderingService (UI/display related functions)
- ConfigurationService (settings and configuration)
- IntegrationService (external service integrations)
- MetadataService (metadata management)
- FileService (file operations)

**OUTPUT FORMAT:**
Provide the result as a JSON object where:
- Keys are service class names
- Values are arrays of method names belonging to that class

**VALIDATION SECTION:**
After the categorization, include:
1. Total method count validation
2. Brief description of each service class's responsibility
3. Estimated line count per service (sum of method line counts)

**INPUT JSON:**
[PASTE THE OUTPUT OF introspect command JSON HERE]
```

3. **Generate Split Classes**
   - Once you have the finalized `split_classes.json`, run the toolkit to generate the split classes:

```bash
java -jar dev-toolkit-1.0.0.jar split <input-file> <output-directory> <mapping-json>
```

**Example:**
```bash
java -jar dev-toolkit-1.0.0.jar split src/main/java/com/example/BigClass.java output/ split_classes.json

# Use constructor injection instead of field injection (default)
java -jar dev-toolkit-1.0.0.jar split src/main/java/com/example/BigClass.java output/ split_classes.json --injection-style=constructor

# Use Play framework annotation style with field injection
java -jar dev-toolkit-1.0.0.jar split src/main/java/com/example/BigClass.java output/ split_classes.json --annotation-style=play --injection-style=field
```

**Options:**
- `--annotation-style`: Specify annotation style (`java` or `play`, default: auto-detect)
- `--injection-style`: Specify dependency injection style (`field` or `constructor`, default: `field`)

**Note:**
- The `split` command now also performs verification internally after splitting, ensuring that all methods listed in your mapping exist in the generated split classes. You can still use the `verify` command separately if you want to re-check later or after manual changes.

**⚠️ Important - Verify Your Results:**
After running the split command, carefully review the generated classes to ensure they meet your expectations. If you're not satisfied with the results, you have several options:

1. **Undo Changes**: Revert to your original class file and start over
2. **Modify Classification**: Update the method groupings in your `split_classes.json` file and re-run the split command
3. **Watch for Cyclic Dependencies**: If the tool detects cyclic dependencies, it will analyze the cycles and propose method redistributions to resolve them. You will be prompted to confirm these changes before they are applied automatically.
4. **Review and Modify Proposed Changes**: When cyclic dependencies are detected, the tool will save proposed changes to `split_classes_proposed.json` and show you exactly what methods will be moved. You can:
   - Review the proposed changes and accept them as-is by entering 'y'
   - Modify the `split_classes_proposed.json` file to customize the changes, then enter 'y' to use your modifications
   - Enter 'n' to cancel and manually modify your original `split_classes.json` instead
5. **Test Compilation and Runtime**: Compile the generated classes to check for compilation issues and run your service to ensure there are no cyclic dependencies or runtime errors

## Dependency Injection Styles

The toolkit supports two dependency injection patterns:

### Field Injection (Default)
- Uses `@Inject` annotations directly on fields
- Fields are not marked as `final`
- No constructor parameters needed
- Example:
```java
public class OriginalClass {
    @Inject
    private ServiceA serviceA;
    
    @Inject 
    private ServiceB serviceB;
    
    public void delegateMethod() {
        return serviceA.someMethod();
    }
}
```

### Constructor Injection
- Uses `@Inject` on constructor with all dependencies as parameters
- Fields are marked as `final`
- All dependencies passed through constructor
- Example:
```java
public class OriginalClass {
    private final ServiceA serviceA;
    private final ServiceB serviceB;
    
    @Inject
    public OriginalClass(ServiceA serviceA, ServiceB serviceB) {
        this.serviceA = serviceA;
        this.serviceB = serviceB;
    }
    
    public void delegateMethod() {
        return serviceA.someMethod();
    }
}
```

**Usage:**
- Default: `--injection-style=field` (can be omitted)
- Constructor injection: `--injection-style=constructor`

---

### Core Commands
- `split`: Split a large Java class into smaller service classes with delegation , it also verifies that none of the methods are lost during the operation
- `verify`: Verify that all methods in mapping exist in generated split classes  
- `introspect`: Extract metadata about all methods in a Java class
- `transform`: Transform a single Play Java file to a Spring-friendly version (by layer)
- `migrate-app`: Migrate an entire Play `app/` tree to a Spring repo’s `src/main/java`
- `generate-prompts`: Generate structured prompts for JUnit test creation with LLMs
- `undoChanges`: Restore original class and remove generated split artifacts using `.split_undo.json`

### Global Options
- `-h, --help`: Show help message
- `-V, --version`: Display version information

## Requirements
- Java 8+
- The mapping file should be a JSON file mapping class names to lists of method names.

## Example Mapping File (`split_classes.json`)
```json
{
  "SiteFeaturePublishService": ["publishMoveToProdFromPreprod", "publishAudience"],
  "SiteConfigAndAssetService": ["moveLessFolderFromSrcToDest", "addLanguageInDefaultConfig"]
}
```

## Output
- After building, only one JAR (`dev-toolkit-1.0.0.jar`) will be generated in the `target/` directory (the version will match your Maven project version).
- Generated classes are written to the specified output directory.
- The original class is refactored to delegate to the new service classes.

## Verification

Use the `verify` command to check that all methods in your mapping exist in the generated classes:

```bash
java -jar dev-toolkit-1.0.0.jar verify <output-directory> <mapping-json>
```

**Example:**
```bash
java -jar dev-toolkit-1.0.0.jar verify output/ split_classes.json
```

**Note:**
- The verification script checks for the presence of all methods listed in your mapping, regardless of their visibility (public, protected, private, or package-private). This ensures that methods are not falsely reported as missing if they are present but not public.

The verify command prints a summary of any missing methods for each class.

---

For more details, see the code and comments in `JavaClassSplitter.java`.