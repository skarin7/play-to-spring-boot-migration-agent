package com.phenom.devtoolkit;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

import static org.junit.Assert.*;

/**
 * JUnit tests for DevToolkitCLI (transform and migrate-app subcommands).
 */
public class DevToolkitCLITest {

    @Rule
    public TemporaryFolder tempFolder = new TemporaryFolder();

    @Test
    public void execute_help_returnsZero() {
        int code = DevToolkitCLI.execute(new String[]{"--help"});
        assertEquals(0, code);
    }

    @Test
    public void execute_version_returnsZero() {
        int code = DevToolkitCLI.execute(new String[]{"-V"});
        assertEquals(0, code);
    }

    @Test
    public void execute_transformCommand_success() throws Exception {
        Path tempDir = tempFolder.getRoot().toPath();
        String playController = "import javax.inject.Singleton;\n\n@Singleton\npublic class HomeController {\n}\n";
        Path input = tempDir.resolve("HomeController.java");
        Path output = tempDir.resolve("out/HomeController.java");
        Files.write(input, playController.getBytes());

        int code = DevToolkitCLI.execute(new String[]{
                "transform",
                "--input", input.toAbsolutePath().toString(),
                "--output", output.toAbsolutePath().toString(),
                "--layer", "controller"
        });

        assertEquals(0, code);
        assertTrue(Files.exists(output));
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("@RestController"));
    }

    @Test
    public void execute_transformCommand_autoLayerFromPath() throws Exception {
        Path tempDir = tempFolder.getRoot().toPath();
        Path inputDir = tempDir.resolve("app/com/foo/controllers");
        Files.createDirectories(inputDir);
        Path input = inputDir.resolve("HomeController.java");
        String playController = "@Singleton\npublic class HomeController {\n}\n";
        Files.write(input, playController.getBytes());
        Path output = tempDir.resolve("out/HomeController.java");
        Files.createDirectories(output.getParent());

        int code = DevToolkitCLI.execute(new String[]{
                "transform",
                "--input", input.toAbsolutePath().toString(),
                "--output", output.toAbsolutePath().toString()
        });

        assertEquals(0, code);
        assertTrue(Files.exists(output));
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("@RestController"));
    }

    @Test
    public void execute_migrateApp_noAppDir_returnsOne() throws Exception {
        Path tempDir = tempFolder.getRoot().toPath();
        // No app/ directory
        int code = DevToolkitCLI.execute(new String[]{
                "migrate-app",
                "--source", tempDir.toAbsolutePath().toString(),
                "--dry-run"
        });
        assertEquals(1, code);
    }

    @Test
    public void execute_migrateApp_dryRun_withAppDir_returnsZero() throws Exception {
        Path tempDir = tempFolder.getRoot().toPath();
        Path appDir = tempDir.resolve("app/com/example/controllers");
        Files.createDirectories(appDir);
        Files.write(appDir.resolve("HomeController.java"), "public class HomeController {}".getBytes());

        int code = DevToolkitCLI.execute(new String[]{
                "migrate-app",
                "--source", tempDir.toAbsolutePath().toString(),
                "--dry-run"
        });

        assertEquals(0, code);
    }

    @Test
    public void pathPrefix_normalizeAndMatch() {
        assertEquals("com/foo", DevToolkitCLI.MigrateAppCommand.normalizeSinglePathPrefix("/com/foo/"));
        assertTrue(DevToolkitCLI.MigrateAppCommand.matchesPathPrefix("com/foo/Bar.java", "com/foo"));
        assertFalse(DevToolkitCLI.MigrateAppCommand.matchesPathPrefix("com/food/Bar.java", "com/foo"));
        assertTrue(DevToolkitCLI.MigrateAppCommand.matchesPathPrefix("com/foo", "com/foo"));
        assertEquals(
                Arrays.asList("a", "b"),
                DevToolkitCLI.MigrateAppCommand.normalizePathPrefixes(Arrays.asList("a/,./b"))
        );
    }

    @Test
    public void execute_migrateApp_pathPrefix_filtersAppOnly() throws Exception {
        Path playRoot = tempFolder.getRoot().toPath().resolve("play");
        Path springRoot = tempFolder.getRoot().toPath().resolve("spring");
        Path pkgA = playRoot.resolve("app/com/example/pkgA");
        Path pkgB = playRoot.resolve("app/com/example/pkgB");
        Files.createDirectories(pkgA);
        Files.createDirectories(pkgB);
        Files.write(pkgA.resolve("A.java"), "public class A {}".getBytes());
        Files.write(pkgB.resolve("B.java"), "public class B {}".getBytes());

        int code = DevToolkitCLI.execute(new String[]{
                "migrate-app",
                "--source", playRoot.toAbsolutePath().toString(),
                "--target", springRoot.toAbsolutePath().toString(),
                "--path-prefix", "com/example/pkgA"
        });

        assertEquals(0, code);
        assertTrue(Files.exists(springRoot.resolve("src/main/java/com/example/pkgA/A.java")));
        assertFalse(Files.exists(springRoot.resolve("src/main/java/com/example/pkgB/B.java")));
    }

    @Test
    public void execute_migrateApp_writesTestsToSpringSrcTestJava() throws Exception {
        Path playRoot = tempFolder.getRoot().toPath().resolve("play");
        Path springRoot = tempFolder.getRoot().toPath().resolve("spring");
        Path appDir = playRoot.resolve("app/com/example");
        Path testDir = playRoot.resolve("src/test/java/com/example");
        Files.createDirectories(appDir);
        Files.createDirectories(testDir);
        Files.write(appDir.resolve("App.java"), "public class App {}".getBytes());
        Files.write(testDir.resolve("AppTest.java"), "public class AppTest {}".getBytes());

        int code = DevToolkitCLI.execute(new String[]{
                "migrate-app",
                "--source", playRoot.toAbsolutePath().toString(),
                "--target", springRoot.toAbsolutePath().toString()
        });

        assertEquals(0, code);
        Path outTest = springRoot.resolve("src/test/java/com/example/AppTest.java");
        assertTrue(Files.exists(outTest));
        Path outMain = springRoot.resolve("src/main/java/com/example/App.java");
        assertTrue(Files.exists(outMain));
    }
}
