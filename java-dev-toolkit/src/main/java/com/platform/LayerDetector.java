package com.platform;

import java.nio.file.Path;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

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
     *
     * <p>Matching is on whole path <em>segments</em>, not substrings. Substring
     * matching on "/controllers/" silently failed for Play's default scaffold
     * layout: this method receives paths relative to the Java source root, so
     * {@code app/controllers/HomeController.java} arrives as
     * {@code controllers/HomeController.java}, which has no leading slash, never
     * matched, and fell through to {@link Layer#OTHER}. Every controller in a
     * default-layout project then migrated in the "other" layer without
     * {@code @RestController}.
     *
     * <p>Segment matching makes the result independent of how much path prefix
     * the caller happens to include, so the repo-root-relative and
     * source-root-relative forms now agree.
     */
    public static Layer classify(Path relativePath) {
        if (relativePath == null) {
            return Layer.OTHER;
        }
        Set<String> directories = directorySegments(relativePath);
        String fileName = fileNameLower(relativePath);

        if (directories.contains("controllers")) {
            return Layer.CONTROLLER;
        }
        if (directories.contains("service") || directories.contains("services")) {
            return Layer.SERVICE;
        }
        // Filename convention shares this branch with /models/, as it always has:
        // db/UserModel.java is a MODEL, not a MANAGER.
        if (directories.contains("models") || fileName.endsWith("model.java")) {
            return Layer.MODEL;
        }
        if (directories.contains("db")) {
            return Layer.MANAGER;
        }
        if (directories.contains("repositories") || directories.contains("dao")) {
            return Layer.REPOSITORY;
        }
        return Layer.OTHER;
    }

    /** Lower-cased directory names in the path, excluding the file name itself. */
    private static Set<String> directorySegments(Path relativePath) {
        Set<String> segments = new HashSet<>();
        int directoryCount = relativePath.getNameCount() - 1;
        for (int i = 0; i < directoryCount; i++) {
            segments.add(relativePath.getName(i).toString().toLowerCase(Locale.ROOT));
        }
        return segments;
    }

    private static String fileNameLower(Path relativePath) {
        Path fileName = relativePath.getFileName();
        return fileName == null ? "" : fileName.toString().toLowerCase(Locale.ROOT);
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
