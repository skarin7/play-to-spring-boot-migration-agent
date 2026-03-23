package com.phenom.devtoolkit;

import java.nio.file.Path;
import java.util.Locale;

/**
 * Path-based layer classification for Play app/ directory layout.
 * Used by migrate-app to choose transformer behavior per file.
 */
public final class LayerDetector {

    public enum Layer {
        CONTROLLER,
        SERVICE,
        MANAGER,
        MODEL,
        REPOSITORY,
        OTHER
    }

    private LayerDetector() {}

    /**
     * Classify a relative path under app/ (e.g. "com/foo/controllers/X.java").
     * Uses forward slashes for comparison (Path normalized to forward slash string).
     */
    public static Layer classify(Path relativePath) {
        if (relativePath == null) {
            return Layer.OTHER;
        }
        String path = relativePath.toString().replace('\\', '/').toLowerCase(Locale.ROOT);
        if (path.contains("/controllers/")) {
            return Layer.CONTROLLER;
        }
        if (path.contains("/service/") || path.contains("/services/")) {
            return Layer.SERVICE;
        }
        if (path.contains("/models/") || path.endsWith("model.java")) {
            return Layer.MODEL;
        }
        if (path.contains("/db/")) {
            return Layer.MANAGER;
        }
        if (path.contains("/repositories/") || path.contains("/dao/")) {
            return Layer.REPOSITORY;
        }
        return Layer.OTHER;
    }

    public static Layer fromString(String value) {
        if (value == null || value.isEmpty()) {
            return Layer.OTHER;
        }
        String v = value.trim().toUpperCase(Locale.ROOT);
        try {
            return Layer.valueOf(v);
        } catch (IllegalArgumentException e) {
            return Layer.OTHER;
        }
    }
}
