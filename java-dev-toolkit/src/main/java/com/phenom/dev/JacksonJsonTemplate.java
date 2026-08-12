package com.phenom.devtoolkit;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Writes a small {@code JacksonJson} helper into the Spring tree when the transformer
 * introduces {@code JacksonJson.readTree} calls.
 */
final class JacksonJsonTemplate {

    private JacksonJsonTemplate() {
    }

    static void writeIfAbsent(Path javaMainRoot, String utilsPackage, boolean dryRun) throws IOException {
        if (utilsPackage == null || utilsPackage.isEmpty()) {
            return;
        }
        Path dir = javaMainRoot.resolve(utilsPackage.replace('.', '/'));
        Path file = dir.resolve("JacksonJson.java");
        if (Files.isRegularFile(file)) {
            return;
        }
        String body = ""
                + "package " + utilsPackage + ";\n\n"
                + "import com.fasterxml.jackson.core.JsonProcessingException;\n"
                + "import com.fasterxml.jackson.databind.JsonNode;\n"
                + "import com.fasterxml.jackson.databind.ObjectMapper;\n\n"
                + "/**\n"
                + " * Wraps {@link ObjectMapper#readTree(String)} so callers do not handle checked\n"
                + " * {@link JsonProcessingException} (Jackson 2.17+).\n"
                + " */\n"
                + "public final class JacksonJson {\n\n"
                + "    private JacksonJson() {\n"
                + "    }\n\n"
                + "    public static JsonNode readTree(ObjectMapper mapper, String json) {\n"
                + "        try {\n"
                + "            return mapper.readTree(json);\n"
                + "        } catch (JsonProcessingException e) {\n"
                + "            throw new IllegalArgumentException(\"Invalid JSON\", e);\n"
                + "        }\n"
                + "    }\n"
                + "}\n";
        if (dryRun) {
            return;
        }
        Files.createDirectories(dir);
        Files.write(file, body.getBytes(StandardCharsets.UTF_8));
    }
}
