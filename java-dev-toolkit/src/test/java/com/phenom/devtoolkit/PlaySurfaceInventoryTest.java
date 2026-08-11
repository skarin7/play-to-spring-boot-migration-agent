package com.phenom.devtoolkit;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.Assert.*;

/**
 * JUnit tests for {@link PlaySurfaceInventory}: classifies Play API touchpoints as
 * KNOWN (a toolkit rewrite rule exists), PARADIGM (structurally unmappable, e.g. Akka actors),
 * or UNKNOWN (a Play API the toolkit doesn't yet transform).
 */
public class PlaySurfaceInventoryTest {

    @Rule
    public TemporaryFolder tempFolder = new TemporaryFolder();

    @Test
    public void scanFile_knownController_classifiesAllTouchpointsKnown() throws Exception {
        String src = "import javax.inject.Singleton;\n"
                + "import play.mvc.Controller;\n"
                + "import play.mvc.Result;\n"
                + "@Singleton\n"
                + "public class HomeController extends Controller {\n"
                + "  public Result index() { return ok(\"hi\"); }\n"
                + "}\n";
        Path file = tempFolder.newFile("HomeController.java").toPath();
        Files.write(file, src.getBytes());

        List<PlaySurfaceInventory.Touchpoint> touchpoints = new PlaySurfaceInventory().scanFile(file);

        assertFalse(touchpoints.isEmpty());
        for (PlaySurfaceInventory.Touchpoint t : touchpoints) {
            assertEquals("expected KNOWN for " + t.construct, "KNOWN", t.classification);
        }
    }

    @Test
    public void scanFile_akkaActor_classifiesParadigm() throws Exception {
        String src = "import akka.actor.UntypedActor;\n"
                + "public class WorkerActor extends UntypedActor {\n"
                + "  public void onReceive(Object msg) {}\n"
                + "}\n";
        Path file = tempFolder.newFile("WorkerActor.java").toPath();
        Files.write(file, src.getBytes());

        List<PlaySurfaceInventory.Touchpoint> touchpoints = new PlaySurfaceInventory().scanFile(file);

        assertTrue("expected at least one touchpoint", touchpoints.size() >= 1);
        assertTrue("expected a PARADIGM touchpoint for the actor",
                touchpoints.stream().anyMatch(t -> "PARADIGM".equals(t.classification)));
    }

    @Test
    public void scanFile_wildcardImport_stillClassifiesKnownUsage() throws Exception {
        // The extremely common `import play.mvc.*;` idiom -- a plain prefix match on the import
        // name ("play.mvc") never matches "play.mvc.Controller"/"play.mvc.Result", so without
        // usage-based resolution this file would wrongly report zero KNOWN touchpoints (or worse,
        // be treated as fully UNKNOWN and skipped by migrate-app, even though it's an ordinary
        // controller the toolkit already knows how to transform).
        String src = "import play.mvc.*;\n"
                + "public class HomeController extends Controller {\n"
                + "  public Result index() { return ok(\"hi\"); }\n"
                + "}\n";
        Path file = tempFolder.newFile("HomeController.java").toPath();
        Files.write(file, src.getBytes());

        List<PlaySurfaceInventory.Touchpoint> touchpoints = new PlaySurfaceInventory().scanFile(file);

        assertFalse("wildcard-imported Controller/Result usage should still be detected", touchpoints.isEmpty());
        assertTrue("every detected touchpoint should be KNOWN (no UNKNOWN/PARADIGM false positive)",
                touchpoints.stream().allMatch(t -> "KNOWN".equals(t.classification)));
    }

    @Test
    public void scanFile_wildcardActorImport_classifiesParadigm() throws Exception {
        String src = "import akka.actor.*;\n"
                + "public class Worker {\n"
                + "  private ActorSystem system;\n"
                + "  void schedule(Scheduler s) {}\n"
                + "}\n";
        Path file = tempFolder.newFile("Worker.java").toPath();
        Files.write(file, src.getBytes());

        List<PlaySurfaceInventory.Touchpoint> touchpoints = new PlaySurfaceInventory().scanFile(file);

        assertTrue("expected at least one touchpoint", touchpoints.size() >= 1);
        assertTrue("expected a PARADIGM touchpoint for ActorSystem/Scheduler via wildcard import",
                touchpoints.stream().anyMatch(t -> "PARADIGM".equals(t.classification)));
    }

    @Test
    public void scanFile_nameCoveredByExplicitImport_notDoubleCountedViaWildcard() throws Exception {
        // Mirrors the real play-java-starter-example AsyncController: an explicit
        // `import akka.actor.ActorSystem;` alongside `import play.mvc.*;`. The wildcard
        // resolver must not also attribute ActorSystem to play.mvc -- that would double-count
        // it (wrong paradigmCount) and mislabel it "play.mvc.ActorSystem", which doesn't exist.
        String src = "import akka.actor.ActorSystem;\n"
                + "import play.mvc.*;\n"
                + "public class AsyncController extends Controller {\n"
                + "  private ActorSystem system;\n"
                + "  public Result index() { return ok(\"hi\"); }\n"
                + "}\n";
        Path file = tempFolder.newFile("AsyncController.java").toPath();
        Files.write(file, src.getBytes());

        List<PlaySurfaceInventory.Touchpoint> touchpoints = new PlaySurfaceInventory().scanFile(file);

        long actorSystemTouchpoints = touchpoints.stream().filter(t -> t.construct.contains("ActorSystem")).count();
        assertEquals("ActorSystem should be counted exactly once, via its explicit import", 1, actorSystemTouchpoints);
        assertTrue("the one ActorSystem touchpoint should carry its real akka.actor package, not play.mvc",
                touchpoints.stream().anyMatch(t -> "akka.actor.ActorSystem".equals(t.construct)));
    }

    @Test
    public void scanFile_playFormApi_classifiesUnknown() throws Exception {
        String src = "import play.data.Form;\n"
                + "import play.data.FormFactory;\n"
                + "public class SignupController {\n"
                + "  private FormFactory formFactory;\n"
                + "  public void bind() { Form<Object> f = formFactory.form(Object.class); }\n"
                + "}\n";
        Path file = tempFolder.newFile("SignupController.java").toPath();
        Files.write(file, src.getBytes());

        List<PlaySurfaceInventory.Touchpoint> touchpoints = new PlaySurfaceInventory().scanFile(file);

        assertTrue("expected at least one touchpoint", touchpoints.size() >= 1);
        assertTrue("expected an UNKNOWN touchpoint for play.data.Form",
                touchpoints.stream().anyMatch(t -> "UNKNOWN".equals(t.classification)));
    }

    @Test
    public void scanTree_computesCoveragePercentAndClassLists() throws Exception {
        Path appDir = tempFolder.getRoot().toPath().resolve("app/controllers");
        Files.createDirectories(appDir);
        Files.write(appDir.resolve("HomeController.java"),
                ("import play.mvc.Controller;\nimport play.mvc.Result;\n"
                        + "public class HomeController extends Controller {\n"
                        + "  public Result index() { return ok(\"hi\"); }\n"
                        + "}\n").getBytes());
        Files.write(appDir.resolve("WorkerActor.java"),
                ("import akka.actor.UntypedActor;\n"
                        + "public class WorkerActor extends UntypedActor {\n"
                        + "  public void onReceive(Object msg) {}\n"
                        + "}\n").getBytes());

        PlaySurfaceInventory.Report report = new PlaySurfaceInventory().scanTree(tempFolder.getRoot().toPath());

        assertTrue(report.knownCount >= 1);
        assertTrue(report.paradigmCount >= 1);
        assertTrue("coverage should be between 0 and 100", report.coveragePercent >= 0.0 && report.coveragePercent <= 100.0);
        assertTrue("coverage should be less than 100 since a PARADIGM item exists", report.coveragePercent < 100.0);
    }

    @Test
    public void cli_migrateApp_skipsParadigmFile_migratesKnownFile_reportsGap() throws Exception {
        Path playRoot = tempFolder.getRoot().toPath().resolve("play");
        Path springRoot = tempFolder.getRoot().toPath().resolve("spring");
        Path controllersDir = playRoot.resolve("app/controllers");
        Path actorsDir = playRoot.resolve("app/actors");
        Files.createDirectories(controllersDir);
        Files.createDirectories(actorsDir);
        Files.write(controllersDir.resolve("HomeController.java"),
                ("import play.mvc.Controller;\nimport play.mvc.Result;\n"
                        + "public class HomeController extends Controller {\n"
                        + "  public Result index() { return ok(\"hi\"); }\n"
                        + "}\n").getBytes());
        Files.write(actorsDir.resolve("WorkerActor.java"),
                ("import akka.actor.UntypedActor;\n"
                        + "public class WorkerActor extends UntypedActor {\n"
                        + "  public void onReceive(Object msg) {}\n"
                        + "}\n").getBytes());
        Path reportPath = tempFolder.getRoot().toPath().resolve("migrate-report.json");

        int code = DevToolkitCLI.execute(new String[]{
                "migrate-app",
                "--source", playRoot.toAbsolutePath().toString(),
                "--target", springRoot.toAbsolutePath().toString(),
                "--report", reportPath.toAbsolutePath().toString()
        });

        assertEquals(0, code);
        assertTrue("known controller should be migrated",
                Files.exists(springRoot.resolve("src/main/java/controllers/HomeController.java")));
        assertFalse("paradigm actor should NOT be written to Spring output",
                Files.exists(springRoot.resolve("src/main/java/actors/WorkerActor.java")));

        String report = new String(Files.readAllBytes(reportPath));
        assertTrue("report should record the skip with its classification and construct",
                report.contains("PARADIGM") && report.contains("akka.actor.UntypedActor"));
    }

    @Test
    public void cli_inventoryCommand_writesJsonReportWithCoverage() throws Exception {
        Path tempDir = tempFolder.getRoot().toPath();
        Path appDir = tempDir.resolve("app/controllers");
        Files.createDirectories(appDir);
        Files.write(appDir.resolve("HomeController.java"),
                ("import play.mvc.Controller;\nimport play.mvc.Result;\n"
                        + "public class HomeController extends Controller {\n"
                        + "  public Result index() { return ok(\"hi\"); }\n"
                        + "}\n").getBytes());
        Path reportPath = tempDir.resolve("inventory-report.json");

        int code = DevToolkitCLI.execute(new String[]{
                "inventory",
                "--source", tempDir.toAbsolutePath().toString(),
                "--report", reportPath.toAbsolutePath().toString()
        });

        assertEquals(0, code);
        assertTrue(Files.exists(reportPath));
        String json = new String(Files.readAllBytes(reportPath));
        assertTrue(json.contains("coveragePercent"));
    }
}
