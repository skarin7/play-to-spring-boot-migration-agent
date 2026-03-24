package com.phenom.devtoolkit;

import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Optional;

import static org.junit.Assert.*;

/**
 * JUnit tests for PlayToSpringTransformer.
 */
public class PlayToSpringTransformerTest {

    @Rule
    public TemporaryFolder tempFolder = new TemporaryFolder();

    private PlayToSpringTransformer transformer;
    private Path tempDir;

    @Before
    public void setUp() throws Exception {
        transformer = new PlayToSpringTransformer();
        tempDir = tempFolder.getRoot().toPath();
    }

    @Test
    public void migrationLayer_testSource_alwaysOther() {
        Path rel = Paths.get("com/foo/controllers/HomeControllerTest.java");
        assertEquals(LayerDetector.Layer.OTHER,
                PlayToSpringTransformer.migrationLayer(rel, PlayToSpringTransformer.PlayMigrationSource.TEST));
    }

    @Test
    public void migrationLayer_application_usesPathClassification() {
        Path rel = Paths.get("com/foo/controllers/HomeController.java");
        assertEquals(LayerDetector.Layer.CONTROLLER,
                PlayToSpringTransformer.migrationLayer(rel, PlayToSpringTransformer.PlayMigrationSource.APPLICATION));
    }

    @Test
    public void resolvePlayTestJavaSourceRoot_prefersMavenLayout() throws Exception {
        Path root = tempFolder.getRoot().toPath();
        Files.createDirectories(root.resolve("test/com/legacy"));
        Files.createDirectories(root.resolve("src/test/java/com/maven"));
        Optional<Path> resolved = PlayToSpringTransformer.resolvePlayTestJavaSourceRoot(root);
        assertTrue(resolved.isPresent());
        assertEquals(root.resolve("src/test/java").normalize(), resolved.get().normalize());
    }

    @Test
    public void resolvePlayTestJavaSourceRoot_fallsBackToLegacyTest() throws Exception {
        Path root = tempFolder.getRoot().toPath();
        Files.createDirectories(root.resolve("test/com/legacy"));
        Optional<Path> resolved = PlayToSpringTransformer.resolvePlayTestJavaSourceRoot(root);
        assertTrue(resolved.isPresent());
        assertEquals(root.resolve("test").normalize(), resolved.get().normalize());
    }

    @Test
    public void transform_other_migratesPlayResultAndStatusImports() throws Exception {
        String src = "import play.mvc.Result;\n"
                + "import static play.mvc.Http.Status.OK;\n"
                + "import static org.junit.Assert.assertEquals;\n"
                + "public class HealthCheckControllerTest {\n"
                + "  void m() {\n"
                + "    Result result = null;\n"
                + "    assertEquals(OK, result.status());\n"
                + "  }\n"
                + "}\n";
        Path input = tempDir.resolve("HealthCheckControllerTest.java");
        Path output = tempDir.resolve("out/HealthCheckControllerTest.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));
        assertFalse(out.contains("import play.mvc.Result"));
        assertFalse(out.contains("import static play.mvc.Http.Status.OK"));
        assertTrue(out.contains("ResponseEntity"));
        assertTrue(out.contains("getStatusCode().value()"));
        assertTrue(out.contains("HttpStatus.OK.value()"));
        assertTrue(out.contains("org.springframework.http.HttpStatus"));
    }

    @Test
    public void transform_other_rewritesPlayHttpRequestMock_usingCompanionController() throws Exception {
        Path root = tempFolder.getRoot().toPath();
        Path main = root.resolve("src/main/java/com/example/WidgetController.java");
        Path test = root.resolve("src/test/java/com/example/WidgetControllerTest.java");
        Files.createDirectories(main.getParent());
        Files.createDirectories(test.getParent());
        Files.write(main, (
                "package com.example;\n"
                        + "import com.fasterxml.jackson.databind.JsonNode;\n"
                        + "import javax.servlet.http.HttpServletRequest;\n"
                        + "import org.springframework.web.bind.annotation.RequestBody;\n"
                        + "public class WidgetController {\n"
                        + "  public void two(JsonNode body, HttpServletRequest r) {}\n"
                        + "  public void one(@RequestBody JsonNode body) {}\n"
                        + "}\n").getBytes());
        Files.write(test, (
                "package com.example;\n"
                        + "import com.fasterxml.jackson.databind.JsonNode;\n"
                        + "import org.junit.Test;\n"
                        + "import org.mockito.InjectMocks;\n"
                        + "import play.mvc.Http;\n"
                        + "import static org.mockito.Mockito.*;\n"
                        + "public class WidgetControllerTest {\n"
                        + "  @InjectMocks WidgetController controller;\n"
                        + "  @Test void t() {\n"
                        + "    JsonNode jsonNode = null;\n"
                        + "    Http.Request request = mock(Http.Request.class);\n"
                        + "    Http.RequestBody rb = mock(Http.RequestBody.class);\n"
                        + "    when(request.body()).thenReturn(rb);\n"
                        + "    when(rb.asJson()).thenReturn(jsonNode);\n"
                        + "    controller.two(request);\n"
                        + "    controller.one(request);\n"
                        + "  }\n"
                        + "}\n").getBytes());
        Path out = root.resolve("out/WidgetControllerTest.java");
        transformer.transform(test, out, LayerDetector.Layer.OTHER);
        String o = new String(Files.readAllBytes(out));
        assertFalse(o.contains("import play.mvc.Http"));
        assertTrue(o.contains("javax.servlet.http.HttpServletRequest"));
        assertTrue(o.contains("controller.two(jsonNode, request)"));
        assertTrue(o.contains("controller.one(jsonNode)"));
    }

    @Test
    public void transform_controller_addsRestController() throws Exception {
        String playController = "import play.mvc.Controller;\n" +
                "import javax.inject.Singleton;\n" +
                "import javax.inject.Inject;\n\n" +
                "@Singleton\n" +
                "public class HomeController {\n" +
                "    @Inject\n" +
                "    SomeService service;\n" +
                "}\n";
        Path input = tempDir.resolve("HomeController.java");
        Path output = tempDir.resolve("out/HomeController.java");
        Files.write(input, playController.getBytes());

        PlayToSpringTransformer.TransformResult result =
                transformer.transform(input, output, LayerDetector.Layer.CONTROLLER);

        assertEquals(input.toString(), result.input);
        assertTrue(result.appliedChanges.stream().anyMatch(s -> s.contains("RestController")));
        assertTrue(Files.exists(output));
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("@RestController"));
        assertTrue(out.contains("org.springframework.web.bind.annotation.RestController"));
        assertTrue(out.contains("@Autowired"));
        assertFalse(out.contains("@Inject"));
    }

    @Test
    public void transform_service_addsServiceAnnotation() throws Exception {
        String playService = "import javax.inject.Singleton;\n\n" +
                "@Singleton\n" +
                "public class UserService {\n" +
                "}\n";
        Path input = tempDir.resolve("UserService.java");
        Path output = tempDir.resolve("out/UserService.java");
        Files.write(input, playService.getBytes());

        PlayToSpringTransformer.TransformResult result =
                transformer.transform(input, output, LayerDetector.Layer.SERVICE);

        assertTrue(result.appliedChanges.stream().anyMatch(s -> s.contains("Service")));
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("@Service"));
        assertTrue(out.contains("org.springframework.stereotype.Service"));
    }

    @Test
    public void transform_service_fieldInjectBecomesAutowired() throws Exception {
        String src = "import javax.inject.Inject;\n\npublic class Svc {\n    @Inject\n    Foo dep;\n}\n";
        Path input = tempDir.resolve("Svc.java");
        Path output = tempDir.resolve("out/Svc.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.SERVICE);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("@Autowired"));
        assertFalse(out.contains("javax.inject.Inject"));
    }

    @Test
    public void transform_otherLayer_googleGuiceInjectBecomesAutowired() throws Exception {
        String src = "import com.google.inject.Inject;\n\npublic class Guicy {\n    @Inject\n    Bar b;\n}\n";
        Path input = tempDir.resolve("Guicy.java");
        Path output = tempDir.resolve("out/Guicy.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("@Autowired"));
        assertFalse(out.contains("com.google.inject.Inject"));
    }

    @Test
    public void transform_service_jsonNewObjectAndDropsPlayImport() throws Exception {
        String src = "import play.libs.Json;\n\nimport javax.inject.Singleton;\n\n@Singleton\n"
                + "public class GraphSvc {\n"
                + "    ObjectNode build() {\n"
                + "        ObjectNode categories = Json.newObject();\n"
                + "        return categories;\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("GraphSvc.java");
        Path output = tempDir.resolve("out/GraphSvc.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.SERVICE);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("objectMapper.createObjectNode()"));
        assertTrue(out.contains("@Autowired") && out.contains("ObjectMapper objectMapper"));
        assertTrue(out.contains("com.fasterxml.jackson.databind.ObjectMapper"));
        assertFalse(out.contains("import play.libs.Json"));
        assertFalse(out.contains("Json.newObject"));
    }

    @Test
    public void transform_other_playWsRestTemplateAndPreDestroy() throws Exception {
        String src = "import play.inject.ApplicationLifecycle;\n"
                + "import play.libs.ws.WSClient;\n"
                + "import play.libs.ws.WSResponse;\n"
                + "import com.fasterxml.jackson.databind.JsonNode;\n"
                + "import java.util.concurrent.CompletableFuture;\n"
                + "public class Blobish {\n"
                + "    WSClient ws;\n"
                + "    com.fasterxml.jackson.databind.ObjectMapper objectMapper;\n"
                + "    Blobish(ApplicationLifecycle lifecycle) {\n"
                + "        lifecycle.addStopHook(() -> { System.err.println(\"stop\"); "
                + "return CompletableFuture.completedFuture(null); });\n"
                + "    }\n"
                + "    JsonNode call(String u) throws Exception {\n"
                + "        return ws.url(u).post(objectMapper.createObjectNode())"
                + ".thenApply(WSResponse::asJson).toCompletableFuture().get();\n"
                + "    }\n"
                + "    void purge() throws Exception {\n"
                + "        WSResponse response = ws.url(\"http://localhost\")"
                + ".setHeader(\"Content-Type\", \"application/json\")"
                + ".post(objectMapper.createObjectNode()).toCompletableFuture().get();\n"
                + "        if (response.getStatus() == 202) { }\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("Blobish.java");
        Path output = tempDir.resolve("out/Blobish.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.fromString("other"));
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("RestTemplate"));
        assertTrue(out.contains("org.springframework.web.client.RestTemplate"));
        assertTrue(out.contains("@PreDestroy"));
        assertTrue(out.contains("playApplicationShutdown"));
        assertTrue(out.contains("JacksonJson.readTree"));
        assertTrue(out.contains("postForEntity"));
        assertTrue(out.contains("getStatusCode().value()"));
        assertFalse(out.contains("WSClient"));
        assertFalse(out.contains("ApplicationLifecycle"));
        assertFalse(out.contains("play.libs.ws"));
    }

    @Test
    public void transform_service_playLoggerToSlf4j() throws Exception {
        String src = "import play.Logger;\nimport com.example.Log;\n\npublic class Loggy {\n"
                + "    private static final Logger.ALogger logger = Log.getLogger(Loggy.class);\n}\n";
        Path input = tempDir.resolve("Loggy.java");
        Path output = tempDir.resolve("out/Loggy.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.SERVICE);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("LoggerFactory.getLogger(Loggy.class)"));
        assertTrue(out.contains("import org.slf4j.LoggerFactory") || out.contains("org.slf4j.LoggerFactory"));
        assertFalse(out.contains("Logger.ALogger"));
    }

    @Test
    public void transform_controller_jsonAndHttpExecution() throws Exception {
        String src = "import play.mvc.Controller;\nimport play.mvc.Result;\nimport play.libs.Json;\n"
                + "import java.util.concurrent.Executor;\n"
                + "import java.util.concurrent.CompletableFuture;\n"
                + "public class Api extends Controller {\n"
                + "    CompletableFuture<Result> x(Executor ex) {\n"
                + "        return CompletableFuture.supplyAsync(() -> ok(Json.toJson(\"a\")), "
                + "play.libs.concurrent.HttpExecution.fromThread(ex));\n"
                + "    }\n"
                + "    void y(com.fasterxml.jackson.databind.JsonNode n) {\n"
                + "        Object o = Json.fromJson(n, String.class);\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("Api.java");
        Path output = tempDir.resolve("out/Api.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.CONTROLLER);
        String out = new String(Files.readAllBytes(output));
        assertFalse(out.contains("extends Controller"));
        assertTrue(out.contains("objectMapper.valueToTree"));
        assertTrue(out.contains("objectMapper.convertValue"));
        assertFalse(out.contains("HttpExecution.fromThread"));
        assertTrue(out.contains("@Autowired"));
        assertTrue(out.contains("ObjectMapper objectMapper"));
        assertTrue(out.contains("CompletableFuture<ResponseEntity<JsonNode>>"));
        assertTrue(out.contains("ResponseEntity.ok("));
        assertFalse(out.contains("play.mvc.Result"));
    }

    @Test
    public void transform_controller_httpRequestToRequestBodyAndServlet() throws Exception {
        String src = "import play.mvc.Controller;\n"
                + "import play.mvc.Http;\n"
                + "import play.mvc.Result;\n"
                + "import java.util.concurrent.CompletableFuture;\n"
                + "public class FilterCtl extends Controller {\n"
                + "    public CompletableFuture<Result> createFilter(Http.Request request) {\n"
                + "        return CompletableFuture.supplyAsync(() -> ok(request.body().asJson()));\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("FilterCtl.java");
        Path output = tempDir.resolve("out/FilterCtl.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.CONTROLLER);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("@RequestBody"));
        assertTrue(out.contains("JsonNode body"));
        assertFalse(out.contains("Http.Request"));
        assertFalse(out.contains("request.body().asJson()"));
        assertTrue(out.contains("ResponseEntity.ok("));
        assertTrue(out.contains("CompletableFuture<ResponseEntity<JsonNode>>"));
        assertFalse(out.contains("play.mvc.Http"));
        assertFalse(out.contains("play.mvc.Result"));
    }

    @Test
    public void transform_controller_httpRequestKeptAsServletWhenStillReferenced() throws Exception {
        String src = "import play.mvc.Controller;\n"
                + "import play.mvc.Http;\n"
                + "import play.mvc.Result;\n"
                + "import com.fasterxml.jackson.databind.JsonNode;\n"
                + "public class Dual extends Controller {\n"
                + "    public Result save(Http.Request request) {\n"
                + "        JsonNode j = request.body().asJson();\n"
                + "        java.util.Objects.requireNonNull(request);\n"
                + "        return ok(j);\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("Dual.java");
        Path output = tempDir.resolve("out/Dual.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.CONTROLLER);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("HttpServletRequest"));
        assertTrue(out.contains("@RequestBody"));
        assertTrue(out.contains("JsonNode body"));
        assertFalse(out.contains("request.body().asJson()"));
        assertTrue(out.contains("requireNonNull(request)"));
    }

    @Test
    public void transform_model_passthroughNoChanges() throws Exception {
        String model = "public class User {\n    private String name;\n}\n";
        Path input = tempDir.resolve("User.java");
        Path output = tempDir.resolve("out/User.java");
        Files.write(input, model.getBytes());

        PlayToSpringTransformer.TransformResult result =
                transformer.transform(input, output, LayerDetector.Layer.MODEL);

        assertTrue(result.appliedChanges.isEmpty());
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("public class User"));
        assertTrue(out.contains("private String name"));
        assertFalse(out.contains("@Service"));
        assertFalse(out.contains("@RestController"));
    }

    @Test
    public void transform_invalidJava_writesOriginalAndWarns() throws Exception {
        String invalid = "not valid java {";
        Path input = tempDir.resolve("Bad.java");
        Path output = tempDir.resolve("out/Bad.java");
        Files.write(input, invalid.getBytes());

        PlayToSpringTransformer.TransformResult result =
                transformer.transform(input, output, LayerDetector.Layer.CONTROLLER);

        assertFalse(result.warnings.isEmpty());
        assertTrue(result.warnings.get(0).toLowerCase().contains("parse"));
        assertTrue(Files.exists(output));
        assertEquals(invalid, new String(Files.readAllBytes(output)));
    }

    @Test
    public void transform_guiceOptionalInjectField_becomesAutowiredRequiredFalse() throws Exception {
        String src = "import com.google.inject.Inject;\n\npublic class OptField {\n"
                + "    @Inject(optional = true)\n    Foo dep;\n}\n";
        Path input = tempDir.resolve("OptField.java");
        Path output = tempDir.resolve("out/OptField.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("required = false") || out.contains("required=false"));
        assertTrue(out.contains("@Autowired"));
        assertFalse(out.contains("@Inject"));
    }

    @Test
    public void transform_guiceOptionalInjectConstructorParam_becomesOptionalType() throws Exception {
        String src = "import com.google.inject.Inject;\n\npublic class OptCtor {\n"
                + "    @Inject\n    OptCtor(@Inject(optional = true) Foo a) {}\n}\n";
        Path input = tempDir.resolve("OptCtor.java");
        Path output = tempDir.resolve("out/OptCtor.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("Optional<Foo>"));
        assertTrue(out.contains("@Autowired"));
    }

    @Test
    public void transform_springStyleAutowiredRequiredFalseOnCtorParam_rewrittenToOptional() throws Exception {
        String src = "import org.springframework.beans.factory.annotation.Autowired;\n\n"
                + "public class BadSpring {\n"
                + "    @Autowired\n"
                + "    public BadSpring(@Autowired(required = false) Foo a) {}\n}\n";
        Path input = tempDir.resolve("BadSpring.java");
        Path output = tempDir.resolve("out/BadSpring.java");
        Files.write(input, src.getBytes());
        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));
        assertTrue(out.contains("Optional<Foo>"));
        assertFalse(out.contains("required = false"));
        assertFalse(out.contains("required=false"));
    }

    @Test
    public void writeReport_writesJsonFile() throws Exception {
        PlayToSpringTransformer.TransformResult result = new PlayToSpringTransformer.TransformResult();
        result.input = "a.java";
        result.output = "b.java";
        result.layer = "controller";
        result.appliedChanges.add("Added @RestController");
        Path reportPath = tempDir.resolve("report/transform-report.json");

        transformer.writeReport(result, reportPath);

        assertTrue(Files.exists(reportPath));
        String json = new String(Files.readAllBytes(reportPath));
        assertTrue(json.contains("a.java"));
        assertTrue(json.contains("controller"));
        assertTrue(json.contains("RestController"));
    }
}
