---
name: play-spring-builder-compact
description: Compact decision-tree skill for Spring project init and compile-fix loops.
---

# Builder: Init Spring Project

IF initialize.status == done → SKIP

STEPS:
1. Check pom.xml already exists (scaffold from start.spring.io — do NOT regenerate)
2. Read build.sbt → add project-specific deps to existing `<dependencies>` block:
   - MongoDB → `spring-boot-starter-data-mongodb`
   - Neo4j → `spring-boot-starter-data-neo4j`
   - Guice → remove (Spring DI replaces it)
   - Other libs → find Maven coordinates, add with version
3. Read conf/application.conf → append project keys to application.properties:
   - `mongodb.uri` → `spring.data.mongodb.uri`
   - `play.server.http.port` → `server.port`
   - Skip `play.http.secret.key`, `akka.*`
4. Verify Application.java package matches `base_package` from workspace.yaml
5. `mvn -q compile` → fix until clean (Spring-only edits)
6. Set initialize.status = done, *_generated = true in migration-status.json

FORBIDDEN:
- Regenerate pom.xml from scratch
- Run migrate-app or dev-toolkit commands
- Step 2/3 loops or full migration

# Builder: Compile-Fix Loop (per slice)

LOOP until mvn compile exits 0:
1. Run `mvn compile`, increment validate_iteration
2. IF errors:
   - Cannot find symbol → add missing import or fix type
   - Method not found → Play→Spring mapping (Result → ResponseEntity)
   - Missing dependency → add to pom.xml
   - Missing class → read Play source, create Spring version
3. Edit ONLY Spring project; never modify Play repo
4. IF error count unchanged for 3 iterations → escalate to user

# Builder: Verification (Step 3)

IF current_step == verify:
1. Count Play *.java under app/ → source_inventory.by_layer
2. Count Spring *.java under src/main/java → migration_verification
3. Compare per-layer: play_expected vs spring_actual
4. Set status: passed | needs_review | failed
5. Set current_step = done
