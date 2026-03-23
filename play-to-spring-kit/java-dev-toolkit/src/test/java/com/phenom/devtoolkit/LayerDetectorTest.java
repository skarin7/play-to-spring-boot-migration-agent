package com.phenom.devtoolkit;

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
