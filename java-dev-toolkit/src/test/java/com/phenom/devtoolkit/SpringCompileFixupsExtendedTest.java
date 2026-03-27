package com.phenom.devtoolkit;

import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.Assert.*;

/**
 * JUnit tests for the ExtendedFixups AST rewrite rules added to SpringCompileFixups:
 *   - javax.inject.Provider<T> → ObjectProvider<T>
 *   - .get() → .getObject() on ObjectProvider
 *   - @Singleton → @Component on non-controller classes
 *   - Idempotence: applying twice produces same result as once
 */
public class SpringCompileFixupsExtendedTest {

    @Rule
    public TemporaryFolder tempFolder = new TemporaryFolder();

    private PlayToSpringTransformer transformer;
    private Path tempDir;

    @Before
    public void setUp() throws Exception {
        transformer = new PlayToSpringTransformer();
        tempDir = tempFolder.getRoot().toPath();
    }

    // -----------------------------------------------------------------------
    //  Provider<T> → ObjectProvider<T>
    // -----------------------------------------------------------------------

    @Test
    public void transform_providerFieldRewrittenToObjectProvider() throws Exception {
        String src =
                "import javax.inject.Provider;\n"
                + "import javax.inject.Inject;\n\n"
                + "public class SomeService {\n"
                + "    @Inject\n"
                + "    private Provider<FooService> fooProvider;\n\n"
                + "    public void doWork() {\n"
                + "        FooService foo = fooProvider.get();\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("SomeService.java");
        Path output = tempDir.resolve("out/SomeService.java");
        Files.write(input, src.getBytes());

        transformer.transform(input, output, LayerDetector.Layer.SERVICE);
        String out = new String(Files.readAllBytes(output));

        // Provider<T> rewritten to ObjectProvider<T>
        assertTrue("Should contain ObjectProvider", out.contains("ObjectProvider<FooService>"));
        assertFalse("Should not contain javax.inject.Provider import", out.contains("javax.inject.Provider"));

        // ObjectProvider import added
        assertTrue("Should import ObjectProvider",
                out.contains("org.springframework.beans.factory.ObjectProvider"));

        // .get() rewritten to .getObject()
        assertTrue("Should contain .getObject()", out.contains("fooProvider.getObject()"));
        assertFalse("Should not contain plain .get() on provider",
                out.contains("fooProvider.get()"));
    }

    @Test
    public void transform_providerParameterRewrittenToObjectProvider() throws Exception {
        String src =
                "import javax.inject.Provider;\n"
                + "import javax.inject.Inject;\n\n"
                + "public class CtorService {\n"
                + "    private final Provider<BarService> barProvider;\n\n"
                + "    @Inject\n"
                + "    public CtorService(Provider<BarService> barProvider) {\n"
                + "        this.barProvider = barProvider;\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("CtorService.java");
        Path output = tempDir.resolve("out/CtorService.java");
        Files.write(input, src.getBytes());

        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));

        // Both field and constructor param should be ObjectProvider
        assertTrue("Field should be ObjectProvider",
                out.contains("ObjectProvider<BarService>"));
        assertFalse("Should not have javax.inject.Provider",
                out.contains("javax.inject.Provider"));
    }

    // -----------------------------------------------------------------------
    //  .get() → .getObject()
    // -----------------------------------------------------------------------

    @Test
    public void transform_getRewrittenToGetObject_onlyOnObjectProviderFields() throws Exception {
        String src =
                "import javax.inject.Provider;\n\n"
                + "public class MixedService {\n"
                + "    private Provider<FooService> fooProvider;\n"
                + "    private java.util.Map<String, String> map;\n\n"
                + "    public void doWork() {\n"
                + "        FooService foo = fooProvider.get();\n"
                + "        String val = map.get(\"key\");\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("MixedService.java");
        Path output = tempDir.resolve("out/MixedService.java");
        Files.write(input, src.getBytes());

        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));

        // fooProvider.get() → fooProvider.getObject()
        assertTrue("Should rewrite provider.get()", out.contains("fooProvider.getObject()"));
        // map.get("key") should NOT be rewritten
        assertTrue("Should preserve map.get()", out.contains("map.get(\"key\")"));
    }

    // -----------------------------------------------------------------------
    //  @Singleton → @Component
    // -----------------------------------------------------------------------

    @Test
    public void transform_singletonToComponent_nonController() throws Exception {
        String src =
                "import javax.inject.Singleton;\n\n"
                + "@Singleton\n"
                + "public class UtilHelper {\n"
                + "    public void help() {}\n"
                + "}\n";
        Path input = tempDir.resolve("UtilHelper.java");
        Path output = tempDir.resolve("out/UtilHelper.java");
        Files.write(input, src.getBytes());

        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));

        assertTrue("Should have @Component", out.contains("@Component"));
        assertFalse("Should not have @Singleton", out.contains("@Singleton"));
        assertTrue("Should import Component",
                out.contains("org.springframework.stereotype.Component"));
        assertFalse("Should not import Singleton",
                out.contains("javax.inject.Singleton"));
    }

    @Test
    public void transform_singletonNotReplacedWhenRestControllerPresent() throws Exception {
        // When transformer already adds @RestController, @Singleton shouldn't become @Component
        String src =
                "import play.mvc.Controller;\n"
                + "import javax.inject.Singleton;\n\n"
                + "@Singleton\n"
                + "public class HomeController extends Controller {\n"
                + "}\n";
        Path input = tempDir.resolve("HomeController.java");
        Path output = tempDir.resolve("out/HomeController.java");
        Files.write(input, src.getBytes());

        transformer.transform(input, output, LayerDetector.Layer.CONTROLLER);
        String out = new String(Files.readAllBytes(output));

        assertTrue("Should have @RestController", out.contains("@RestController"));
        // @Singleton should have been removed by the controller stereotype logic, not replaced with @Component
        assertFalse("Should not have @Singleton after controller transform", out.contains("@Singleton"));
    }

    // -----------------------------------------------------------------------
    //  Idempotence
    // -----------------------------------------------------------------------

    @Test
    public void transform_extendedFixups_idempotent() throws Exception {
        String src =
                "import javax.inject.Provider;\n"
                + "import javax.inject.Inject;\n"
                + "import javax.inject.Singleton;\n\n"
                + "@Singleton\n"
                + "public class IdempotentService {\n"
                + "    @Inject\n"
                + "    private Provider<FooService> fooProvider;\n\n"
                + "    public void work() {\n"
                + "        FooService foo = fooProvider.get();\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("IdempotentService.java");
        Path firstOutput = tempDir.resolve("out1/IdempotentService.java");
        Path secondOutput = tempDir.resolve("out2/IdempotentService.java");
        Files.write(input, src.getBytes());

        // First application
        transformer.transform(input, firstOutput, LayerDetector.Layer.OTHER);
        String firstResult = new String(Files.readAllBytes(firstOutput));

        // Second application (apply again to the already-transformed output)
        transformer.transform(firstOutput, secondOutput, LayerDetector.Layer.OTHER);
        String secondResult = new String(Files.readAllBytes(secondOutput));

        assertEquals("Second application should be identical to first (idempotent)",
                firstResult, secondResult);
    }

    // -----------------------------------------------------------------------
    //  Round-trip: Provider<T> + .get() → ObjectProvider<T> + .getObject(), no javax.inject.Provider left
    // -----------------------------------------------------------------------

    @Test
    public void transform_providerRoundTrip_noJavaxInjectProviderRemains() throws Exception {
        String src =
                "import javax.inject.Provider;\n"
                + "import javax.inject.Inject;\n\n"
                + "public class RoundTripService {\n"
                + "    private Provider<FooService> fooProvider;\n"
                + "    private Provider<BarService> barProvider;\n\n"
                + "    @Inject\n"
                + "    public RoundTripService(Provider<FooService> fooProvider, Provider<BarService> barProvider) {\n"
                + "        this.fooProvider = fooProvider;\n"
                + "        this.barProvider = barProvider;\n"
                + "    }\n\n"
                + "    public void work() {\n"
                + "        FooService foo = fooProvider.get();\n"
                + "        BarService bar = barProvider.get();\n"
                + "    }\n"
                + "}\n";
        Path input = tempDir.resolve("RoundTripService.java");
        Path output = tempDir.resolve("out/RoundTripService.java");
        Files.write(input, src.getBytes());

        transformer.transform(input, output, LayerDetector.Layer.OTHER);
        String out = new String(Files.readAllBytes(output));

        // No javax.inject.Provider anywhere (imports or types)
        assertFalse("Should have no javax.inject.Provider reference",
                out.contains("javax.inject.Provider"));
        assertFalse("Should have no plain 'Provider<' type reference",
                out.matches("(?s).*\\bProvider<\\w+>.*"));

        // ObjectProvider present for both
        assertTrue(out.contains("ObjectProvider<FooService>"));
        assertTrue(out.contains("ObjectProvider<BarService>"));

        // .getObject() calls present
        assertTrue(out.contains("fooProvider.getObject()"));
        assertTrue(out.contains("barProvider.getObject()"));
    }
}
