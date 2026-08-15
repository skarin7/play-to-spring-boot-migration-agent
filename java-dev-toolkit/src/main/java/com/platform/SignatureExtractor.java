package com.platform;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.Problem;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Modifier;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.type.Type;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * Emits a coarse structural signature per Java file, for comparing a Play source
 * tree against its migrated Spring output.
 *
 * <p>Purpose: catch a class that was made to compile by hollowing it out. A file
 * whose body is replaced with {@code return null;} still counts as one migrated
 * file, so file-count verification scores it as success. Comparing signatures
 * catches it.
 *
 * <p>Deliberately coarse. Migration is *supposed* to rewrite bodies --
 * {@code play.mvc.Result} becomes {@code ResponseEntity}, Guice becomes
 * constructor injection -- so recording exact types or exact statement text would
 * flag every correctly migrated file. What is recorded is only what should
 * survive any faithful migration: which public methods exist, roughly how much
 * logic each contains, and whether a method still returns a value at all.
 */
public final class SignatureExtractor {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private SignatureExtractor() {}

    /** Coarse return-type buckets. Exact type names change legitimately; these do not. */
    public enum ReturnKind {
        VOID,
        PRIMITIVE,
        REFERENCE
    }

    static ReturnKind returnKind(Type type) {
        if (type.isVoidType()) {
            return ReturnKind.VOID;
        }
        return type.isPrimitiveType() ? ReturnKind.PRIMITIVE : ReturnKind.REFERENCE;
    }

    static String visibility(MethodDeclaration method) {
        if (method.hasModifier(Modifier.Keyword.PUBLIC)) {
            return "public";
        }
        if (method.hasModifier(Modifier.Keyword.PROTECTED)) {
            return "protected";
        }
        if (method.hasModifier(Modifier.Keyword.PRIVATE)) {
            return "private";
        }
        return "package";
    }

    /**
     * Statements inside the body, excluding the body block itself.
     *
     * <p>Nested statements count, so a method whose logic sits inside one big
     * {@code if} is not scored as a single statement. An abstract or interface
     * method has no body and scores 0.
     */
    static int statementCount(MethodDeclaration method) {
        return method.getBody()
                .map(SignatureExtractor::countWithin)
                .orElse(0);
    }

    private static int countWithin(BlockStmt body) {
        // findAll includes the block itself; it is scaffolding, not logic.
        return Math.max(0, body.findAll(Statement.class).size() - 1);
    }

    /** Structural signature of one parsed compilation unit. */
    static ObjectNode signatureOf(CompilationUnit cu, String relativePath) {
        ObjectNode root = MAPPER.createObjectNode();
        root.put("path", relativePath);

        String primaryType = cu.getTypes().stream()
                .findFirst()
                .map(TypeDeclaration::getNameAsString)
                .orElse("");
        root.put("class", primaryType);

        ArrayNode methods = root.putArray("methods");
        List<MethodDeclaration> declarations = new ArrayList<>(cu.findAll(MethodDeclaration.class));
        declarations.sort(Comparator
                .comparing(MethodDeclaration::getNameAsString)
                .thenComparingInt(m -> m.getParameters().size()));
        for (MethodDeclaration method : declarations) {
            ObjectNode node = methods.addObject();
            node.put("name", method.getNameAsString());
            node.put("arity", method.getParameters().size());
            node.put("visibility", visibility(method));
            node.put("returns", returnKind(method.getType()).name().toLowerCase());
            node.put("statements", statementCount(method));
        }

        ArrayNode fields = root.putArray("fields");
        cu.findAll(FieldDeclaration.class).stream()
                .flatMap(f -> f.getVariables().stream())
                .map(VariableDeclarator::getNameAsString)
                .sorted()
                .forEach(fields::add);

        return root;
    }

    /** Parse one file. Returns a node carrying {@code parse_error} if unparseable. */
    public static ObjectNode extractFile(Path file, String relativePath) {
        try (InputStream in = Files.newInputStream(file)) {
            ParseResult<CompilationUnit> parsed = new JavaParser().parse(in);
            // A partial parse still yields a CompilationUnit, just one missing the
            // methods it failed to read. Reporting that as a signature would look
            // like methods vanished during migration -- a false blocker. Only a
            // fully successful parse produces a signature.
            if (!parsed.isSuccessful() || !parsed.getResult().isPresent()) {
                return parseError(relativePath, describeProblems(parsed));
            }
            return signatureOf(parsed.getResult().get(), relativePath);
        } catch (IOException | RuntimeException e) {
            return parseError(relativePath, String.valueOf(e.getMessage()));
        }
    }

    private static String describeProblems(ParseResult<CompilationUnit> parsed) {
        if (parsed.getProblems().isEmpty()) {
            return "could not parse";
        }
        return parsed.getProblems().stream()
                .map(Problem::getMessage)
                .limit(3)
                .collect(Collectors.joining("; "));
    }

    private static ObjectNode parseError(String relativePath, String message) {
        ObjectNode node = MAPPER.createObjectNode();
        node.put("path", relativePath);
        node.put("parse_error", message);
        return node;
    }

    /**
     * Signatures for every {@code *.java} under {@code root}, keyed by path
     * relative to {@code root} so the Play and Spring trees line up.
     */
    public static ObjectNode extractTree(Path root) throws IOException {
        ObjectNode result = MAPPER.createObjectNode();
        result.put("root", root.toString());
        ObjectNode files = result.putObject("files");
        if (!Files.isDirectory(root)) {
            return result;
        }
        try (Stream<Path> walk = Files.walk(root)) {
            walk.filter(Files::isRegularFile)
                    .filter(p -> p.toString().endsWith(".java"))
                    .sorted()
                    .forEach(p -> {
                        String rel = root.relativize(p).toString().replace('\\', '/');
                        files.set(rel, extractFile(p, rel));
                    });
        }
        return result;
    }

    public static String toJson(ObjectNode node) throws IOException {
        return MAPPER.writerWithDefaultPrettyPrinter().writeValueAsString(node);
    }
}
