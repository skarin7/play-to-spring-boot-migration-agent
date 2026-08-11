package com.phenom.devtoolkit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

/**
 * Tests for structural signature extraction, the input to the T2 preservation check.
 */
public class SignatureExtractorTest {

    @Rule
    public TemporaryFolder folder = new TemporaryFolder();

    private static final String SERVICE = String.join("\n",
        "package com.acme.services;",
        "public class ContentService {",
        "    private final ContentRepository repo;",
        "    private String cacheKey;",
        "    public ContentService(ContentRepository repo) { this.repo = repo; }",
        "    public String search(String q, int limit) {",
        "        String normalized = q.trim();",
        "        if (normalized.isEmpty()) {",
        "            return \"\";",
        "        }",
        "        return repo.find(normalized, limit);",
        "    }",
        "    private void audit() { }",
        "}");

    /** Same class after being hollowed out to make the build pass. */
    private static final String SERVICE_STUBBED = String.join("\n",
        "package com.acme.services;",
        "public class ContentService {",
        "    private final ContentRepository repo;",
        "    private String cacheKey;",
        "    public ContentService(ContentRepository repo) { this.repo = repo; }",
        "    public String search(String q, int limit) {",
        "        return null;",
        "    }",
        "    private void audit() { }",
        "}");

    private ObjectNode extract(String source, String name) throws Exception {
        File file = folder.newFile(name);
        Files.write(file.toPath(), source.getBytes(StandardCharsets.UTF_8));
        return SignatureExtractor.extractFile(file.toPath(), name);
    }

    private JsonNode method(ObjectNode signature, String name) {
        for (JsonNode m : signature.get("methods")) {
            if (name.equals(m.get("name").asText())) {
                return m;
            }
        }
        throw new AssertionError("no method named " + name);
    }

    @Test
    public void extractsClassNameAndFields() throws Exception {
        ObjectNode sig = extract(SERVICE, "ContentService.java");
        assertEquals("ContentService", sig.get("class").asText());
        assertEquals(2, sig.get("fields").size());
        assertEquals("cacheKey", sig.get("fields").get(0).asText());
        assertEquals("repo", sig.get("fields").get(1).asText());
    }

    @Test
    public void recordsArityVisibilityAndReturnKind() throws Exception {
        ObjectNode sig = extract(SERVICE, "ContentService.java");

        JsonNode search = method(sig, "search");
        assertEquals(2, search.get("arity").asInt());
        assertEquals("public", search.get("visibility").asText());
        assertEquals("reference", search.get("returns").asText());

        JsonNode audit = method(sig, "audit");
        assertEquals("private", audit.get("visibility").asText());
        assertEquals("void", audit.get("returns").asText());
    }

    @Test
    public void countsNestedStatementsNotJustTopLevel() throws Exception {
        // Body: local var, if, return-inside-if, trailing return. A method whose
        // logic sits inside one if-block must not score as a single statement.
        JsonNode search = method(extract(SERVICE, "ContentService.java"), "search");
        assertTrue("expected several statements, got " + search.get("statements").asInt(),
                search.get("statements").asInt() >= 4);
    }

    @Test
    public void stubbedMethodCollapsesToOneStatement() throws Exception {
        JsonNode stubbed = method(extract(SERVICE_STUBBED, "ContentService.java"), "search");
        assertEquals(1, stubbed.get("statements").asInt());
    }

    /** The signal T2 keys on: same signature, drastically less logic. */
    @Test
    public void stubbingIsVisibleAsAStatementDrop() throws Exception {
        int before = method(extract(SERVICE, "A.java"), "search").get("statements").asInt();
        int after = method(extract(SERVICE_STUBBED, "B.java"), "search").get("statements").asInt();
        assertTrue("stub should lose most statements", after < before * 0.4);
        assertTrue("stub should be tiny", after < 3);
    }

    @Test
    public void emptyBodyScoresZero() throws Exception {
        ObjectNode sig = extract(String.join("\n",
            "public class Empty {",
            "    public void nothing() { }",
            "}"), "Empty.java");
        assertEquals(0, method(sig, "nothing").get("statements").asInt());
    }

    @Test
    public void interfaceMethodWithoutBodyScoresZero() throws Exception {
        ObjectNode sig = extract(String.join("\n",
            "public interface Repo {",
            "    String find(String q);",
            "}"), "Repo.java");
        assertEquals(0, method(sig, "find").get("statements").asInt());
        assertEquals("Repo", sig.get("class").asText());
    }

    @Test
    public void overloadsAreKeptSeparate() throws Exception {
        ObjectNode sig = extract(String.join("\n",
            "public class Over {",
            "    public void go() { int a = 1; }",
            "    public void go(int n) { int a = 1; int b = 2; }",
            "}"), "Over.java");
        assertEquals(2, sig.get("methods").size());
        assertEquals(0, sig.get("methods").get(0).get("arity").asInt());
        assertEquals(1, sig.get("methods").get(1).get("arity").asInt());
    }

    @Test
    public void unparseableFileReportsErrorInsteadOfThrowing() throws Exception {
        ObjectNode sig = extract("this is not java {{{", "Broken.java");
        assertTrue(sig.has("parse_error"));
    }

    /**
     * JavaParser returns a usable CompilationUnit even when the parse failed, so
     * a truncated file yields a signature that is simply missing methods. Emitting
     * that would read as "these methods were dropped during migration" -- a false
     * blocker on a file that is merely corrupt. It must be reported as a parse
     * error instead.
     */
    @Test
    public void partiallyParsedFileIsReportedAsErrorNotAsMissingMethods() throws Exception {
        ObjectNode sig = extract(String.join("\n",
            "public class Truncated {",
            "    public String alive() { return \"x\"; }",
            "    public void broken( {"), "Truncated.java");
        assertTrue("partial parse must not masquerade as a valid signature",
                sig.has("parse_error"));
    }

    @Test
    public void treeExtractionKeysByRelativePath() throws Exception {
        Path root = folder.newFolder("src").toPath();
        Path nested = root.resolve("com/acme/services");
        Files.createDirectories(nested);
        Files.write(nested.resolve("ContentService.java"),
                SERVICE.getBytes(StandardCharsets.UTF_8));

        ObjectNode tree = SignatureExtractor.extractTree(root);
        assertTrue(tree.get("files").has("com/acme/services/ContentService.java"));
        assertEquals("ContentService",
                tree.get("files").get("com/acme/services/ContentService.java")
                    .get("class").asText());
    }

    @Test
    public void missingDirectoryYieldsNoFiles() throws Exception {
        ObjectNode tree = SignatureExtractor.extractTree(
                folder.getRoot().toPath().resolve("does-not-exist"));
        assertEquals(0, tree.get("files").size());
    }
}
