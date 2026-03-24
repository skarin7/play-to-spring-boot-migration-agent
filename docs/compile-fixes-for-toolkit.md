# Spring migration compile fixes (canonical)

This document tracks recurring **Play → Spring** compile failures and where they are handled. It lives in the **migration-agent repo** so it is not duplicated in every Spring project. Per-project `.migration/` may keep **logs only** (e.g. `mvn-compile-latest.log`).

## Automated in java-dev-toolkit (`SpringCompileFixups` + `JacksonJsonTemplate`)

| Symptom | Fix applied by transformer / migrate-app |
|--------|------------------------------------------|
| `HttpStatusCode.getReasonPhrase()` (Spring 6) | `getStatusCode()` wrapped: `instanceof HttpStatus` → `getReasonPhrase()`, else `toString()` |
| `CompletableFuture` inference vs `ResponseEntity<JsonNode>` (controllers) | `CompletableFuture.<ResponseEntity<JsonNode>>supplyAsync(...)` |
| Neo4j `ServerAddressResolver.resolve` returns `List<ServerAddress>` | Return type → `Set<ServerAddress>`; `new ArrayList`/`LinkedList` in those methods → `LinkedHashSet` |
| Bare `UNAUTHORIZED` / `BAD_REQUEST` / … as first arg to `getErrorResult` | → `HttpStatus.*.value()` |
| Play-style `request.getHeaders().get("h")` on servlet | → `Optional.ofNullable(request.getHeader("h"))` |
| `objectMapper.readTree(String)` / checked `JsonProcessingException` | → `JacksonJson.readTree(objectMapper, json)`; **migrate-app** writes `JacksonJson.java` under inferred `…utils` package when needed |
| `ResponseEntity.ok("literal")` with `ResponseEntity<JsonNode>` return (controllers) | → `ok(objectMapper.valueToTree("…"))` when class has `objectMapper` field |

## Spring Boot component scan (`@SpringBootApplication`)

| Symptom | Cause | Fix |
|--------|--------|-----|
| `required a bean of type '…db.RedisManager' that could not be found` (class has `@Component`) | `Application.java` is in a **subpackage** (e.g. `com.acme.app.utils`) | Move `Application` to the **app root package** (e.g. `com.acme.app`) **or** add `@SpringBootApplication(scanBasePackages = "com.acme.app")`. Default scan is only the application class’s package and children. |

**Kit:** `play-to-spring-kit/scripts/setup.sh` normalizes `base_package` in `workspace.yaml` by stripping trailing segments such as `utils`, `controllers`, `service`, `db`, etc., so the builder does not place `Application.java` under a leaf folder by mistake.

## Spring Boot runtime (`mvn spring-boot:run`) — wiring and config

Symptoms below appear **after** `mvn compile` succeeds. Fixes applied for `spring-cms-content-service` (March 2026); encode the same patterns in **builder** / **transformer** where noted.

| Symptom | Cause | Fix |
|--------|--------|-----|
| `S3Manager` / `BlobManager` bean not found; `StorageManager` needs both | `javax.inject.@Singleton` is **not** a Spring bean; only one implementation may be needed | Register **`@Component`** (Spring). Use **`@ConditionalOnExpression`** on `S3Manager` / `BlobManager` so only the implementation matching `storage.account.type` loads (`S3` vs `blob`, case-insensitive). **`StorageManager`**: `@Autowired(required = false)` on `S3Manager` and `BlobManager` constructor params. |
| `RestTemplate` bean not found | Spring Boot does not define a `RestTemplate` bean by default | Add `@Configuration` with `@Bean RestTemplate restTemplate()` (e.g. `…config.RestTemplateConfig`). |
| `BlobManager` NPE on startup when Azure config missing | Constructor assumed non-null URLs | Null-safe checks on optional strings; prefer **not** loading `BlobManager` when `storage.account.type` is not `blob` (conditional bean). |
| `KafkaProducer` / `Properties.put` NPE | `bootstrap.servers.config` null but `raiseEventsToKafka` true | **`EventsProducerService`**: if bootstrap blank, skip creating producer (log warn). Default client id when missing. **`EventsConsumerService`**: same for bootstrap + require `enriched_content_topic`; skip consumer thread if unset. |
| `No qualifying bean of type 'javax.inject.Provider<…>'` | Guice `Provider<T>` is not a Spring bean type | Replace field type with **`org.springframework.beans.factory.ObjectProvider<T>`** and calls **`provider.getObject()`** (not `get()`). |
| `Parameter 0 … required a bean of type 'java.lang.String'` on `@Service` constructor | Play/Guice injected a **named** `String`; Spring tries to resolve any `String` bean | Remove bogus `String` ctor parameters; derive values from **`PhenomConfig`** inside the constructor (e.g. `EventProcessor(PhenomConfig)` only). |
| `Failed to configure a DataSource` / no driver | `spring-boot-starter-jdbc` on classpath without `spring.datasource.*` | If the app uses **Mongo/Neo4j/Redis only**, exclude **`DataSourceAutoConfiguration`** in `application.properties`, or add a real JDBC URL + driver, or drop JDBC starter if unused. |
| `PhenomThreadPool` / `getInt("phenompool.size")` throws | `getInt` has no default overload used | Use **`getInt(key, defaultValue)`** when the key may be absent. |
| `GraphManager` NPE on `AuthTokens.basic` when graph unset | Empty Play config | If `graph.url` or `graph.userName` blank, **return null** driver early (log warn). **`@PreDestroy`**: guard **`if (driver == null) return`** before `close()`. |

**Toolkit follow-up (optional):** AST pass to rewrite `javax.inject.Provider<` → `ObjectProvider<`, adjust `.get()` → `.getObject()` on those fields; replace `javax.inject.@Singleton` on `assets/*` with `@Component` when class also has Spring `@Autowired` fields.

## Still manual or follow-up toolkit work

| Symptom | Fix | Owner |
|--------|-----|--------|
| `sendPostSync` overload ambiguity (`Map` vs `JsonNode`) | Disambiguate with `valueToTree` / explicit `JsonNode` local | Transformer (future) or builder |
| Shallow migrated utility classes (`ContentUtil`, …) | Port full Play implementation; add deps (e.g. `commons-lang3`) | Builder + pom |
| `HttpServletRequest` vs `play.mvc.Http.Request` at service boundary | `new Http.Request(request)` or refactor service API | Transformer (future) or builder |
| Lambda / outer name collisions (`JsonNode body`) | Rename inner binding | Transformer (future) or builder |

## References

- `java-dev-toolkit/src/main/java/com/phenom/devtoolkit/SpringCompileFixups.java`
- `java-dev-toolkit/src/main/java/com/phenom/devtoolkit/JacksonJsonTemplate.java`
