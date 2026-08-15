package com.platform;

import org.junit.Test;

import java.nio.file.Paths;

import static org.junit.Assert.assertEquals;

/**
 * JUnit tests for LayerDetector path-based layer classification.
 */
public class LayerDetectorTest {

    @Test
    public void classify_nullPath_returnsOther() {
        assertEquals(LayerDetector.Layer.OTHER, LayerDetector.classify(null));
    }

    @Test
    public void classify_controllersPath_returnsController() {
        assertEquals(LayerDetector.Layer.CONTROLLER,
                LayerDetector.classify(Paths.get("com/foo/controllers/MyController.java")));
        assertEquals(LayerDetector.Layer.CONTROLLER,
                LayerDetector.classify(Paths.get("app/controllers/HomeController.java")));
    }

    @Test
    public void classify_servicePath_returnsService() {
        assertEquals(LayerDetector.Layer.SERVICE,
                LayerDetector.classify(Paths.get("com/foo/service/MyService.java")));
        assertEquals(LayerDetector.Layer.SERVICE,
                LayerDetector.classify(Paths.get("app/services/UserService.java")));
    }

    @Test
    public void classify_modelsPath_returnsModel() {
        assertEquals(LayerDetector.Layer.MODEL,
                LayerDetector.classify(Paths.get("com/foo/models/User.java")));
        assertEquals(LayerDetector.Layer.MODEL,
                LayerDetector.classify(Paths.get("app/model.java")));
    }

    @Test
    public void classify_dbPath_returnsManager() {
        assertEquals(LayerDetector.Layer.MANAGER,
                LayerDetector.classify(Paths.get("com/foo/db/SomeManager.java")));
    }

    @Test
    public void classify_repositoriesPath_returnsRepository() {
        assertEquals(LayerDetector.Layer.REPOSITORY,
                LayerDetector.classify(Paths.get("com/foo/repositories/UserRepository.java")));
        assertEquals(LayerDetector.Layer.REPOSITORY,
                LayerDetector.classify(Paths.get("app/dao/UserDao.java")));
    }

    @Test
    public void classify_unknownPath_returnsOther() {
        assertEquals(LayerDetector.Layer.OTHER,
                LayerDetector.classify(Paths.get("com/foo/utils/Helper.java")));
    }

    /**
     * PlayToSpringTransformer passes paths relative to the Java source root, so a
     * default Play scaffold (app/controllers/HomeController.java, package
     * controllers) arrives here as "controllers/HomeController.java" with no
     * leading slash. The former substring match on "/controllers/" missed it and
     * returned OTHER, so every controller migrated in the "other" layer without
     * @RestController. The existing tests above never caught this because they
     * all pass repo-root-relative paths, which is not the form used at runtime.
     */
    @Test
    public void classify_sourceRootRelativePaths_matchTopLevelDirectories() {
        assertEquals(LayerDetector.Layer.CONTROLLER,
                LayerDetector.classify(Paths.get("controllers/HomeController.java")));
        assertEquals(LayerDetector.Layer.SERVICE,
                LayerDetector.classify(Paths.get("services/SearchService.java")));
        assertEquals(LayerDetector.Layer.SERVICE,
                LayerDetector.classify(Paths.get("service/LegacyService.java")));
        assertEquals(LayerDetector.Layer.MODEL,
                LayerDetector.classify(Paths.get("models/User.java")));
        assertEquals(LayerDetector.Layer.MANAGER,
                LayerDetector.classify(Paths.get("db/MongoManager.java")));
        assertEquals(LayerDetector.Layer.REPOSITORY,
                LayerDetector.classify(Paths.get("repositories/UserRepository.java")));
        assertEquals(LayerDetector.Layer.REPOSITORY,
                LayerDetector.classify(Paths.get("dao/UserDao.java")));
    }

    /** Both path forms must agree, whatever prefix the caller includes. */
    @Test
    public void classify_isIndependentOfPathPrefix() {
        assertEquals(LayerDetector.classify(Paths.get("controllers/X.java")),
                LayerDetector.classify(Paths.get("app/controllers/X.java")));
        assertEquals(LayerDetector.classify(Paths.get("com/foo/models/U.java")),
                LayerDetector.classify(Paths.get("app/com/foo/models/U.java")));
    }

    /** Segment matching must not fire on names that merely contain a keyword. */
    @Test
    public void classify_partialDirectoryNames_doNotMatch() {
        assertEquals(LayerDetector.Layer.OTHER,
                LayerDetector.classify(Paths.get("com/foo/servicehelpers/Helper.java")));
        assertEquals(LayerDetector.Layer.OTHER,
                LayerDetector.classify(Paths.get("com/foo/mycontrollers/X.java")));
        // Singular "model" is a package name, not the models layer directory.
        assertEquals(LayerDetector.Layer.OTHER,
                LayerDetector.classify(Paths.get("com/foo/model/Thing.java")));
    }

    /** Precedence is unchanged: the *Model.java convention still outranks /db/. */
    @Test
    public void classify_precedenceUnchanged() {
        assertEquals(LayerDetector.Layer.MODEL,
                LayerDetector.classify(Paths.get("com/foo/db/UserModel.java")));
        // /db/ is still tested before /repositories/.
        assertEquals(LayerDetector.Layer.MANAGER,
                LayerDetector.classify(Paths.get("com/foo/db/repositories/Foo.java")));
        assertEquals(LayerDetector.Layer.CONTROLLER,
                LayerDetector.classify(Paths.get("com/foo/controllers/ViewModel.java")));
    }

    @Test
    public void classify_bareFileName_returnsOther() {
        assertEquals(LayerDetector.Layer.OTHER,
                LayerDetector.classify(Paths.get("Module.java")));
    }

    @Test
    public void classify_isCaseInsensitive() {
        assertEquals(LayerDetector.Layer.CONTROLLER,
                LayerDetector.classify(Paths.get("Controllers/HomeController.java")));
    }

    @Test
    public void fromString_nullOrEmpty_returnsOther() {
        assertEquals(LayerDetector.Layer.OTHER, LayerDetector.fromString(null));
        assertEquals(LayerDetector.Layer.OTHER, LayerDetector.fromString(""));
        assertEquals(LayerDetector.Layer.OTHER, LayerDetector.fromString("   "));
    }

    @Test
    public void fromString_validLayer_returnsLayer() {
        assertEquals(LayerDetector.Layer.CONTROLLER, LayerDetector.fromString("controller"));
        assertEquals(LayerDetector.Layer.CONTROLLER, LayerDetector.fromString("CONTROLLER"));
        assertEquals(LayerDetector.Layer.SERVICE, LayerDetector.fromString("service"));
        assertEquals(LayerDetector.Layer.REPOSITORY, LayerDetector.fromString("repository"));
        assertEquals(LayerDetector.Layer.MODEL, LayerDetector.fromString("model"));
        assertEquals(LayerDetector.Layer.MANAGER, LayerDetector.fromString("manager"));
        assertEquals(LayerDetector.Layer.OTHER, LayerDetector.fromString("other"));
    }

    @Test
    public void fromString_invalid_returnsOther() {
        assertEquals(LayerDetector.Layer.OTHER, LayerDetector.fromString("invalid"));
        assertEquals(LayerDetector.Layer.OTHER, LayerDetector.fromString("unknown"));
    }
}
