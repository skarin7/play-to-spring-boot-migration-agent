package com.phenom.devtoolkit;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.Modifier;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.BooleanLiteralExpr;
import com.github.javaparser.ast.expr.ClassExpr;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MarkerAnnotationExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.Name;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.Type;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Transforms Play-oriented Java toward Spring conventions: stereotypes, DI, logging,
 * and (for controllers) structural cleanup, Json/async rewrites, {@code Http.Request} /
 * {@code Result} / {@code ok()}-style response mapping toward Spring MVC types.
 * <p>
 * Static helpers {@link #migrationLayer(Path, PlayMigrationSource)} and
 * {@link #resolvePlayTestJavaSourceRoot(Path)} support bulk migration (e.g. {@code migrate-app}).
 */
public class PlayToSpringTransformer {

    /**
     * Which Play source tree a file belongs to when migrating to Spring. Drives
     * {@link #migrationLayer(Path, PlayMigrationSource)}: test sources stay on {@link LayerDetector.Layer#OTHER}
     * so JUnit classes under {@code controllers/} are not given {@code @RestController}.
     */
    public enum PlayMigrationSource {
        APPLICATION,
        TEST
    }

    /**
     * Resolves the transformer layer for a path relative to a Play Java root ({@code app/}, {@code test/}, or {@code src/test/java}).
     */
    public static LayerDetector.Layer migrationLayer(Path relativePath, PlayMigrationSource sourceRoot) {
        if (sourceRoot == PlayMigrationSource.TEST) {
            return LayerDetector.Layer.OTHER;
        }
        return LayerDetector.classify(relativePath);
    }

    /**
     * Maven-style Play tests live under {@code src/test/java}; legacy layout uses {@code repo/test/}.
     * Prefers {@code src/test/java} when that directory exists.
     */
    public static Optional<Path> resolvePlayTestJavaSourceRoot(Path playProjectRoot) {
        Path maven = playProjectRoot.resolve("src").resolve("test").resolve("java");
        if (Files.isDirectory(maven)) {
            return Optional.of(maven);
        }
        Path legacy = playProjectRoot.resolve("test");
        if (Files.isDirectory(legacy)) {
            return Optional.of(legacy);
        }
        return Optional.empty();
    }

    public static class TransformResult {
        public String input;
        public String output;
        public String layer;
        public List<String> appliedChanges = new ArrayList<>();
        public List<String> warnings = new ArrayList<>();
        public List<String> errors = new ArrayList<>();
        /** When set, {@code migrate-app} may emit {@code JacksonJson} under this package (e.g. {@code com.acme.utils}). */
        public String jacksonJsonUtilsPackage;
    }

    private final JavaParser parser = new JavaParser();
    private final ObjectMapper objectMapper = new ObjectMapper();

    private static final Set<String> SPRING_STEREOTYPES = new HashSet<>(Arrays.asList(
            "Component", "Service", "Repository", "Controller", "RestController", "Configuration"
    ));

    /**
     * Transform a Play Java file to Spring based on layer. Paths may be relative; caller should resolve.
     */
    public TransformResult transform(Path input, Path output, LayerDetector.Layer layer) throws IOException {
        TransformResult result = new TransformResult();
        result.input = input.toString();
        result.output = output.toString();
        result.layer = layer.name().toLowerCase();

        String source = new String(Files.readAllBytes(input));

        ParseResult<CompilationUnit> parseResult = parser.parse(source);
        if (!parseResult.isSuccessful() || !parseResult.getResult().isPresent()) {
            Files.createDirectories(output.getParent());
            Files.write(output, source.getBytes());
            result.warnings.add("Parse failed; wrote original source without changes");
            return result;
        }

        CompilationUnit cu = parseResult.getResult().get();
        Optional<ClassOrInterfaceDeclaration> clazzOpt = cu.findFirst(ClassOrInterfaceDeclaration.class);
        if (!clazzOpt.isPresent()) {
            Files.createDirectories(output.getParent());
            Files.write(output, source.getBytes());
            result.warnings.add("No class declaration found; wrote original without changes");
            return result;
        }

        ClassOrInterfaceDeclaration clazz = clazzOpt.get();

        switch (layer) {
            case CONTROLLER:
                applyControllerStereotype(clazz, cu, result);
                break;
            case SERVICE:
                applyServiceOrManager(clazz, cu, result, "Service", "org.springframework.stereotype.Service");
                break;
            case MANAGER:
                applyServiceOrManager(clazz, cu, result, "Component", "org.springframework.stereotype.Component");
                break;
            case REPOSITORY:
                applyServiceOrManager(clazz, cu, result, "Repository", "org.springframework.stereotype.Repository");
                break;
            case MODEL:
            case OTHER:
            default:
                break;
        }

        applyInjectToAutowired(cu, result);
        applyLoggerToSlf4j(cu, result);

        boolean usedOm = applyPlayJsonApiRewrites(cu, result);
        if (usedOm) {
            ensureObjectMapperField(clazz, cu, result);
        }

        boolean wsNeedsOm = new PlayWsSpringRewriter(parser).apply(clazz, cu, result);
        if (wsNeedsOm) {
            ensureObjectMapperField(clazz, cu, result);
        }

        if (layer == LayerDetector.Layer.CONTROLLER) {
            applyControllerStructural(clazz, cu, result);
            applyControllerHttpExecutionRewrites(cu, result);
            applyControllerHttpRequestAndResponse(clazz, cu, result);
        } else if (layer == LayerDetector.Layer.OTHER && importsPlayFramework(cu)) {
            // JUnit / util sources: same Play MVC cleanup as controllers (Result, Status.OK, Results.ok, imports).
            applyPlayMvcNonControllerRewrites(clazz, cu, input, result);
        }

        SpringCompileFixups.apply(cu, clazz, layer, result);

        prunePlayLibsJsonImportIfUnused(cu, result);
        removePlayMvcImports(cu, result);
        pruneUnusedInjectImports(cu);

        String rendered = cu.toString();
        Files.createDirectories(output.getParent());
        Files.write(output, rendered.getBytes());
        return result;
    }

    private void applyControllerStereotype(ClassOrInterfaceDeclaration clazz, CompilationUnit cu, TransformResult result) {
        if (!hasSpringStereotype(clazz)) {
            removePlayAnnotations(clazz, result);
            clazz.addAnnotation(new MarkerAnnotationExpr("RestController"));
            ensureImport(cu, "org.springframework.web.bind.annotation.RestController");
            result.appliedChanges.add("Added @RestController");
        }
    }

    private void applyServiceOrManager(ClassOrInterfaceDeclaration clazz, CompilationUnit cu,
                                       TransformResult result, String annotationName, String importFqcn) {
        if (!hasSpringStereotype(clazz)) {
            removePlayAnnotations(clazz, result);
            clazz.addAnnotation(new MarkerAnnotationExpr(annotationName));
            ensureImport(cu, importFqcn);
            result.appliedChanges.add("Added @" + annotationName);
        }
    }

    private void removePlayAnnotations(ClassOrInterfaceDeclaration clazz, TransformResult result) {
        List<String> toRemove = Arrays.asList("Singleton", "Inject");
        boolean removed = clazz.getAnnotations().stream()
                .anyMatch(a -> toRemove.contains(a.getNameAsString()));
        if (removed) {
            clazz.getAnnotations().removeIf(a -> toRemove.contains(a.getNameAsString()));
            result.appliedChanges.add("Removed class-level @Singleton / @Inject");
        }
    }

    private boolean hasSpringStereotype(ClassOrInterfaceDeclaration clazz) {
        return clazz.getAnnotations().stream()
                .anyMatch(a -> SPRING_STEREOTYPES.contains(a.getNameAsString()));
    }

    private void applyInjectToAutowired(CompilationUnit cu, TransformResult result) {
        boolean changed = false;
        for (ClassOrInterfaceDeclaration type : cu.findAll(ClassOrInterfaceDeclaration.class)) {
            for (FieldDeclaration fd : type.getFields()) {
                if (replaceInjectOnFieldOrMethodAnnotations(fd.getAnnotations(), cu, result)) {
                    changed = true;
                }
            }
            for (MethodDeclaration md : type.getMethods()) {
                if (replaceInjectOnFieldOrMethodAnnotations(md.getAnnotations(), cu, result)) {
                    changed = true;
                }
                for (Parameter p : md.getParameters()) {
                    if (replaceInjectOnCallableParameter(p, cu, result, false, md)) {
                        changed = true;
                    }
                }
            }
            for (ConstructorDeclaration cd : type.getConstructors()) {
                if (replaceInjectOnConstructorDeclarationAnnotations(cd.getAnnotations(), cu, result)) {
                    changed = true;
                }
                for (Parameter p : cd.getParameters()) {
                    if (replaceInjectOnCallableParameter(p, cu, result, true, cd)) {
                        changed = true;
                    }
                }
            }
        }
        if (changed) {
            ensureImport(cu, "org.springframework.beans.factory.annotation.Autowired");
            result.appliedChanges.add(
                    "Replaced @Inject with @Autowired / java.util.Optional (Spring: optional ctor params use Optional<T>)");
        }
    }

    /**
     * Fields and setter/injection methods: {@code @Inject(optional=true)} → {@code @Autowired(required=false)}.
     * Marker {@code @Inject} → {@code @Autowired}.
     */
    private boolean replaceInjectOnFieldOrMethodAnnotations(
            NodeList<AnnotationExpr> annotations, CompilationUnit cu, TransformResult result) {
        List<AnnotationExpr> copy = new ArrayList<>(annotations);
        boolean replaced = false;
        annotations.clear();
        for (AnnotationExpr ann : copy) {
            if (!isInjectAnnotation(ann)) {
                annotations.add(ann);
                continue;
            }
            replaced = true;
            if (injectIsOptional(ann)) {
                annotations.add(autowiredRequiredFalseAnnotation());
                result.appliedChanges.add("@Inject(optional=true) → @Autowired(required=false)");
            } else {
                annotations.add(new MarkerAnnotationExpr("Autowired"));
            }
        }
        return replaced;
    }

    /**
     * Constructor-level {@code @Inject}. Guice forbids {@code optional} on constructors; if present, still map to
     * {@code @Autowired} and warn — prefer {@code Optional<T>} on individual parameters instead.
     */
    private boolean replaceInjectOnConstructorDeclarationAnnotations(
            NodeList<AnnotationExpr> annotations, CompilationUnit cu, TransformResult result) {
        List<AnnotationExpr> copy = new ArrayList<>(annotations);
        boolean replaced = false;
        annotations.clear();
        for (AnnotationExpr ann : copy) {
            if (!isInjectAnnotation(ann)) {
                annotations.add(ann);
                continue;
            }
            replaced = true;
            if (injectIsOptional(ann)) {
                result.warnings.add(
                        "Constructor had @Inject(optional=true) (invalid in Guice); mapped to @Autowired — use @Inject(optional=true) on parameters + Optional<T> instead");
            }
            annotations.add(new MarkerAnnotationExpr("Autowired"));
        }
        return replaced;
    }

    /**
     * Constructor/method parameter: optional {@code @Inject} → {@code Optional<Type>} and no param annotation;
     * required {@code @Inject} → {@code @Autowired} on parameter.
     */
    private boolean replaceInjectOnCallableParameter(
            Parameter p,
            CompilationUnit cu,
            TransformResult result,
            boolean isConstructor,
            Object parentCallable) {
        List<AnnotationExpr> copy = new ArrayList<>(p.getAnnotations());
        boolean changed = false;
        p.getAnnotations().clear();
        for (AnnotationExpr ann : copy) {
            if (!isInjectAnnotation(ann)) {
                p.getAnnotations().add(ann);
                continue;
            }
            changed = true;
            if (injectIsOptional(ann)) {
                wrapParameterTypeInOptional(p, cu);
                if (isConstructor && parentCallable instanceof ConstructorDeclaration) {
                    ensureAutowiredOnConstructor((ConstructorDeclaration) parentCallable, cu);
                } else if (!isConstructor && parentCallable instanceof MethodDeclaration) {
                    ensureAutowiredOnMethod((MethodDeclaration) parentCallable, cu);
                }
                result.appliedChanges.add(
                        "Optional constructor/method param: @Inject(optional=true) → Optional<…> (update body to use Optional API if needed)");
            } else {
                p.addAnnotation(new MarkerAnnotationExpr("Autowired"));
            }
        }
        return changed;
    }

    private static NormalAnnotationExpr autowiredRequiredFalseAnnotation() {
        return new NormalAnnotationExpr(
                new Name("Autowired"),
                new NodeList<>(new MemberValuePair("required", new BooleanLiteralExpr(false))));
    }

    private static boolean injectIsOptional(AnnotationExpr ann) {
        if (!(ann instanceof NormalAnnotationExpr)) {
            return false;
        }
        NormalAnnotationExpr n = (NormalAnnotationExpr) ann;
        for (MemberValuePair pair : n.getPairs()) {
            if ("optional".equals(pair.getNameAsString()) && pair.getValue().isBooleanLiteralExpr()) {
                return pair.getValue().asBooleanLiteralExpr().getValue();
            }
        }
        return false;
    }

    private void wrapParameterTypeInOptional(Parameter p, CompilationUnit cu) {
        Type t = p.getType();
        if (isJavaUtilOptionalType(t)) {
            ensureImport(cu, "java.util.Optional");
            return;
        }
        Type inner = t.clone();
        ClassOrInterfaceType wrapped = new ClassOrInterfaceType(null, "Optional");
        wrapped.setTypeArguments(new NodeList<>(inner));
        p.setType(wrapped);
        ensureImport(cu, "java.util.Optional");
    }

    private static boolean isJavaUtilOptionalType(Type t) {
        if (!(t instanceof ClassOrInterfaceType)) {
            return false;
        }
        ClassOrInterfaceType c = (ClassOrInterfaceType) t;
        return "Optional".equals(c.getNameAsString()) && c.getTypeArguments().isPresent();
    }

    private void ensureAutowiredOnConstructor(ConstructorDeclaration cd, CompilationUnit cu) {
        if (!hasAutowiredMeta(cd.getAnnotations())) {
            cd.addAnnotation(new MarkerAnnotationExpr("Autowired"));
            ensureImport(cu, "org.springframework.beans.factory.annotation.Autowired");
        }
    }

    private void ensureAutowiredOnMethod(MethodDeclaration md, CompilationUnit cu) {
        if (!hasAutowiredMeta(md.getAnnotations())) {
            md.addAnnotation(new MarkerAnnotationExpr("Autowired"));
            ensureImport(cu, "org.springframework.beans.factory.annotation.Autowired");
        }
    }

    private static boolean hasAutowiredMeta(NodeList<AnnotationExpr> anns) {
        return anns.stream().anyMatch(PlayToSpringTransformer::isAutowiredNamedAnnotation);
    }

    private static boolean isAutowiredNamedAnnotation(AnnotationExpr a) {
        String n = a.getNameAsString();
        return "Autowired".equals(n) || n.endsWith(".Autowired");
    }

    private boolean isInjectAnnotation(AnnotationExpr ann) {
        String name = ann.getNameAsString();
        return "Inject".equals(name);
    }

    private void pruneUnusedInjectImports(CompilationUnit cu) {
        boolean stillHasInject = cu.findAll(AnnotationExpr.class).stream()
                .anyMatch(this::isInjectAnnotation);
        if (stillHasInject) {
            return;
        }
        cu.getImports().removeIf(i -> {
            String n = i.getNameAsString();
            return "javax.inject.Inject".equals(n) || "com.google.inject.Inject".equals(n);
        });
    }

    private void applyLoggerToSlf4j(CompilationUnit cu, TransformResult result) {
        List<FieldDeclaration> toTransform = new ArrayList<>();
        for (FieldDeclaration fd : cu.findAll(FieldDeclaration.class)) {
            if (isPlayLoggerField(fd)) {
                toTransform.add(fd);
            }
        }
        for (FieldDeclaration fd : toTransform) {
            Optional<ClassOrInterfaceDeclaration> enc = fd.findAncestor(ClassOrInterfaceDeclaration.class);
            if (!enc.isPresent()) {
                continue;
            }
            String className = enc.get().getNameAsString();
            String loggerName = fd.getVariable(0).getNameAsString();
            ClassOrInterfaceType loggerType = new ClassOrInterfaceType(null, "Logger");
            MethodCallExpr factoryCall = new MethodCallExpr(
                    new NameExpr("LoggerFactory"),
                    "getLogger",
                    new NodeList<>(new ClassExpr(new ClassOrInterfaceType(null, className))));
            VariableDeclarator loggerVar = new VariableDeclarator(loggerType, loggerName, factoryCall);
            FieldDeclaration replacement = new FieldDeclaration(
                    new NodeList<>(Modifier.privateModifier(), Modifier.staticModifier(), Modifier.finalModifier()),
                    new NodeList<>(loggerVar));
            fd.replace(replacement);
            ensureImport(cu, "org.slf4j.Logger");
            ensureImport(cu, "org.slf4j.LoggerFactory");
            result.appliedChanges.add("Replaced Play Logger.ALogger / Log.getLogger with SLF4J");
        }
        cu.getImports().removeIf(i -> "play.Logger".equals(i.getNameAsString()));
        if (!referencesSimpleName(cu, "Log")) {
            cu.getImports().removeIf(i -> i.getNameAsString().endsWith(".Log") && !i.isAsterisk());
        }
    }

    private boolean isPlayLoggerField(FieldDeclaration fd) {
        if (fd.getVariables().isEmpty()) {
            return false;
        }
        String typeStr = fd.getElementType().asString();
        if (typeStr.contains("ALogger") || typeStr.endsWith("Logger.ALogger")) {
            return true;
        }
        return fd.getVariable(0).getInitializer()
                .filter(MethodCallExpr.class::isInstance)
                .map(e -> (MethodCallExpr) e)
                .filter(m -> "getLogger".equals(m.getNameAsString()))
                .filter(m -> m.getScope().map(s -> "Log".equals(s.toString())).orElse(false))
                .isPresent();
    }

    private boolean referencesSimpleName(CompilationUnit cu, String name) {
        return cu.findAll(NameExpr.class).stream().anyMatch(n -> name.equals(n.getNameAsString()));
    }

    private void applyControllerStructural(ClassOrInterfaceDeclaration clazz, CompilationUnit cu, TransformResult result) {
        int removedExt = (int) clazz.getExtendedTypes().stream()
                .filter(e -> "Controller".equals(e.getNameAsString()))
                .count();
        clazz.getExtendedTypes().removeIf(e -> "Controller".equals(e.getNameAsString()));
        if (removedExt > 0) {
            result.appliedChanges.add("Removed extends Controller");
        }
        int removedBp = 0;
        for (MethodDeclaration md : clazz.getMethods()) {
            long n = md.getAnnotations().stream().filter(this::isBodyParserAnnotation).count();
            if (n > 0) {
                md.getAnnotations().removeIf(this::isBodyParserAnnotation);
                removedBp += n;
            }
        }
        if (removedBp > 0) {
            result.appliedChanges.add("Removed @BodyParser annotations (" + removedBp + ")");
        }
        cu.getImports().removeIf(i -> "play.mvc.Controller".equals(i.getNameAsString()));
        cu.getImports().removeIf(i -> "play.mvc.BodyParser".equals(i.getNameAsString()));
    }

    private boolean isBodyParserAnnotation(AnnotationExpr a) {
        String n = a.getNameAsString();
        return n.contains("BodyParser");
    }

    /**
     * Rewrites Play {@code play.libs.Json} helpers to Jackson (all layers): {@code newObject}/{@code newArray}/
     * {@code fromJson}/{@code toJson}/{@code parse} via injected {@code ObjectMapper} ({@code createObjectNode},
     * {@code createArrayNode}, {@code convertValue}, {@code valueToTree}, {@code readTree}).
     *
     * @return true if an {@code ObjectMapper} field is required
     */
    private boolean applyPlayJsonApiRewrites(CompilationUnit cu, TransformResult result) {
        boolean needOm = false;
        boolean anyPass;
        do {
            anyPass = false;
            List<MethodCallExpr> allCalls = new ArrayList<>(cu.findAll(MethodCallExpr.class));
            for (MethodCallExpr m : allCalls) {
                if (!isPlayLibsJsonScopedCall(m)) {
                    continue;
                }
                String name = m.getNameAsString();
                if ("fromJson".equals(name) && m.getArguments().size() == 2) {
                    MethodCallExpr conv = new MethodCallExpr(new NameExpr("objectMapper"), "convertValue");
                    conv.setArguments(m.getArguments());
                    m.replace(conv);
                    needOm = true;
                    anyPass = true;
                    result.appliedChanges.add("Replaced Json.fromJson with objectMapper.convertValue");
                } else if ("toJson".equals(name) && m.getArguments().size() == 1) {
                    MethodCallExpr tree = new MethodCallExpr(new NameExpr("objectMapper"), "valueToTree");
                    tree.setArguments(m.getArguments());
                    m.replace(tree);
                    needOm = true;
                    anyPass = true;
                    result.appliedChanges.add("Replaced Json.toJson with objectMapper.valueToTree");
                } else if ("parse".equals(name) && m.getArguments().size() == 1) {
                    MethodCallExpr read = new MethodCallExpr(new NameExpr("objectMapper"), "readTree");
                    read.setArguments(m.getArguments());
                    m.replace(read);
                    needOm = true;
                    anyPass = true;
                    result.appliedChanges.add("Replaced Json.parse with objectMapper.readTree");
                } else if ("newObject".equals(name) && m.getArguments().isEmpty()) {
                    m.replace(new MethodCallExpr(new NameExpr("objectMapper"), "createObjectNode", new NodeList<>()));
                    needOm = true;
                    anyPass = true;
                    result.appliedChanges.add("Replaced Json.newObject with objectMapper.createObjectNode");
                } else if ("newArray".equals(name) && m.getArguments().isEmpty()) {
                    m.replace(new MethodCallExpr(new NameExpr("objectMapper"), "createArrayNode", new NodeList<>()));
                    needOm = true;
                    anyPass = true;
                    result.appliedChanges.add("Replaced Json.newArray with objectMapper.createArrayNode");
                }
            }
        } while (anyPass);
        if (needOm) {
            ensureImport(cu, "com.fasterxml.jackson.databind.ObjectMapper");
        }
        return needOm;
    }

    private boolean isPlayLibsJsonScopedCall(MethodCallExpr m) {
        return m.getScope().filter(this::isPlayJsonScopeExpression).isPresent();
    }

    private boolean isPlayJsonScopeExpression(Expression scope) {
        String str = scope.toString();
        return "Json".equals(str) || "play.libs.Json".equals(str) || str.endsWith(".Json");
    }

    private void prunePlayLibsJsonImportIfUnused(CompilationUnit cu, TransformResult result) {
        if (stillReferencesPlayLibsJsonCalls(cu)) {
            return;
        }
        int before = cu.getImports().size();
        cu.getImports().removeIf(i -> "play.libs.Json".equals(i.getNameAsString()));
        int removed = before - cu.getImports().size();
        if (removed > 0) {
            result.appliedChanges.add("Removed unused play.libs.Json import");
        }
    }

    private boolean stillReferencesPlayLibsJsonCalls(CompilationUnit cu) {
        return cu.findAll(MethodCallExpr.class).stream().anyMatch(this::isPlayLibsJsonScopedCall);
    }

    /** Controllers only: {@code HttpExecution.fromThread(e)} -> {@code e}. */
    private void applyControllerHttpExecutionRewrites(CompilationUnit cu, TransformResult result) {
        List<MethodCallExpr> allCalls = new ArrayList<>(cu.findAll(MethodCallExpr.class));
        for (MethodCallExpr m : allCalls) {
            if (isHttpExecutionFromThread(m)) {
                if (!m.getArguments().isEmpty()) {
                    m.replace(m.getArgument(0));
                    result.appliedChanges.add("Replaced HttpExecution.fromThread(executor) with executor");
                }
            }
        }
        cu.getImports().removeIf(i -> "play.libs.concurrent.HttpExecution".equals(i.getNameAsString()));
    }

    private boolean isHttpExecutionFromThread(MethodCallExpr m) {
        if (!"fromThread".equals(m.getNameAsString()) || m.getArguments().size() != 1) {
            return false;
        }
        return m.getScope().map(s -> s.toString().contains("HttpExecution")).orElse(false);
    }

    private void ensureObjectMapperField(ClassOrInterfaceDeclaration clazz, CompilationUnit cu, TransformResult result) {
        if (hasFieldNamed(clazz, "objectMapper")) {
            return;
        }
        ClassOrInterfaceType omType = new ClassOrInterfaceType(null, "ObjectMapper");
        VariableDeclarator vd = new VariableDeclarator(omType, "objectMapper");
        FieldDeclaration fd = new FieldDeclaration(new NodeList<>(Modifier.privateModifier()), new NodeList<>(vd));
        fd.addAnnotation(new MarkerAnnotationExpr("Autowired"));
        ensureImport(cu, "org.springframework.beans.factory.annotation.Autowired");
        ensureImport(cu, "com.fasterxml.jackson.databind.ObjectMapper");
        int insertAt = 0;
        for (int i = 0; i < clazz.getMembers().size(); i++) {
            if (clazz.getMember(i) instanceof FieldDeclaration) {
                insertAt = i + 1;
            }
        }
        clazz.getMembers().add(insertAt, fd);
        result.appliedChanges.add("Added @Autowired ObjectMapper objectMapper field");
    }

    private boolean hasFieldNamed(ClassOrInterfaceDeclaration clazz, String name) {
        return clazz.getFields().stream()
                .flatMap(f -> f.getVariables().stream())
                .anyMatch(v -> name.equals(v.getNameAsString()));
    }

    /**
     * Replace {@code Http.Request} with {@code @RequestBody JsonNode} + optional {@code HttpServletRequest},
     * rewrite Play {@code Result} types to {@code ResponseEntity<JsonNode>}, and map {@code ok}/{@code created}/etc.
     */
    private void applyControllerHttpRequestAndResponse(ClassOrInterfaceDeclaration clazz, CompilationUnit cu,
                                                       TransformResult result) {
        boolean changed = false;
        for (MethodDeclaration md : clazz.getMethods()) {
            if (applyHttpRequestParameterMigration(md, cu, result)) {
                changed = true;
            }
        }
        List<MethodDeclaration> methods = new ArrayList<>(clazz.getMethods());
        for (MethodDeclaration md : methods) {
            List<MethodCallExpr> calls = new ArrayList<>(md.findAll(MethodCallExpr.class));
            for (MethodCallExpr m : calls) {
                if (rewritePlayResultFactoryCall(m, result)) {
                    changed = true;
                }
            }
        }
        boolean resultTypesChanged = false;
        for (MethodDeclaration md : clazz.getMethods()) {
            if (replacePlayResultInType(md.getType())) {
                resultTypesChanged = true;
            }
        }
        if (resultTypesChanged) {
            changed = true;
            result.appliedChanges.add("Replaced Result return type(s) with ResponseEntity<JsonNode>");
        }
        if (changed) {
            ensureImport(cu, "org.springframework.http.ResponseEntity");
            ensureImport(cu, "com.fasterxml.jackson.databind.JsonNode");
        }
        cu.getImports().removeIf(i -> "play.mvc.Result".equals(i.getNameAsString()));
        cu.getImports().removeIf(i -> "play.mvc.Http".equals(i.getNameAsString()));
    }

    private boolean applyHttpRequestParameterMigration(MethodDeclaration md, CompilationUnit cu, TransformResult result) {
        Optional<Parameter> httpReq = md.getParameters().stream()
                .filter(p -> isPlayHttpRequestType(p.getType()))
                .findFirst();
        if (!httpReq.isPresent()) {
            return false;
        }
        Parameter playParam = httpReq.get();
        String paramName = playParam.getNameAsString();
        String jsonName = chooseJsonBodyParameterName(md, playParam);
        List<MethodCallExpr> asJsonCalls = md.findAll(MethodCallExpr.class).stream()
                .filter(m -> isRequestBodyAsJsonCall(m, paramName))
                .collect(Collectors.toList());
        for (MethodCallExpr m : asJsonCalls) {
            m.replace(new NameExpr(jsonName));
        }
        if (!asJsonCalls.isEmpty()) {
            result.appliedChanges.add("Replaced " + paramName + ".body().asJson() with " + jsonName);
        }
        boolean stillUsesParam = md.findAll(NameExpr.class).stream()
                .anyMatch(n -> paramName.equals(n.getNameAsString()));
        NodeList<Parameter> params = md.getParameters();
        int idx = params.indexOf(playParam);
        if (idx < 0) {
            return false;
        }
        if (stillUsesParam) {
            playParam.setType(new ClassOrInterfaceType(null, "HttpServletRequest"));
            ensureImport(cu, "javax.servlet.http.HttpServletRequest");
            result.appliedChanges.add("Replaced Http.Request " + paramName + " with HttpServletRequest");
        } else {
            params.remove(playParam);
            result.appliedChanges.add("Removed Http.Request parameter (body only → @RequestBody)");
        }
        if (!asJsonCalls.isEmpty()) {
            Parameter jsonParam = new Parameter(new ClassOrInterfaceType(null, "JsonNode"), jsonName);
            jsonParam.addAnnotation(new MarkerAnnotationExpr("RequestBody"));
            ensureImport(cu, "com.fasterxml.jackson.databind.JsonNode");
            ensureImport(cu, "org.springframework.web.bind.annotation.RequestBody");
            params.add(0, jsonParam);
        }
        return true;
    }

    private String chooseJsonBodyParameterName(MethodDeclaration md, Parameter playParam) {
        Set<String> taken = new HashSet<>();
        for (Parameter p : md.getParameters()) {
            if (p != playParam) {
                taken.add(p.getNameAsString());
            }
        }
        if (!taken.contains("body")) {
            return "body";
        }
        if (!taken.contains("requestBody")) {
            return "requestBody";
        }
        return "jsonBody";
    }

    private boolean isPlayHttpRequestType(Type type) {
        String s = type.asString().replace(" ", "");
        return "Http.Request".equals(s) || s.endsWith(".Http.Request") || s.contains("play.mvc.Http.Request");
    }

    private boolean isRequestBodyAsJsonCall(MethodCallExpr m, String requestParamName) {
        if (!"asJson".equals(m.getNameAsString()) || !m.getArguments().isEmpty()) {
            return false;
        }
        Optional<MethodCallExpr> bodyCall = m.getScope()
                .filter(MethodCallExpr.class::isInstance)
                .map(MethodCallExpr.class::cast);
        if (!bodyCall.isPresent() || !"body".equals(bodyCall.get().getNameAsString())
                || !bodyCall.get().getArguments().isEmpty()) {
            return false;
        }
        return bodyCall.get().getScope()
                .filter(NameExpr.class::isInstance)
                .map(NameExpr.class::cast)
                .map(NameExpr::getNameAsString)
                .filter(requestParamName::equals)
                .isPresent();
    }

    private boolean replacePlayResultInType(Type type) {
        if (!(type instanceof ClassOrInterfaceType)) {
            return false;
        }
        ClassOrInterfaceType c = (ClassOrInterfaceType) type;
        boolean changed = false;
        String as = c.asString().replace(" ", "");
        if ("Result".equals(as) || "play.mvc.Result".equals(as)) {
            c.setName("ResponseEntity");
            c.removeScope();
            c.setTypeArguments(new NodeList<>(new ClassOrInterfaceType(null, "JsonNode")));
            return true;
        }
        if (c.getTypeArguments().isPresent()) {
            for (Type arg : c.getTypeArguments().get()) {
                if (replacePlayResultInType(arg)) {
                    changed = true;
                }
            }
        }
        return changed;
    }

    /**
     * Rewrite Play {@code ok}/{@code badRequest}/{@code created}/… helpers to {@link org.springframework.http.ResponseEntity}.
     */
    private boolean rewritePlayResultFactoryCall(MethodCallExpr m, TransformResult result) {
        String name = m.getNameAsString();
        int nargs = m.getArguments().size();
        if (!isPlayMvcHelperScope(m)) {
            return false;
        }
        if ("ok".equals(name)) {
            MethodCallExpr rep;
            if (nargs == 0) {
                rep = new MethodCallExpr(
                        new MethodCallExpr(new NameExpr("ResponseEntity"), "ok"),
                        "build", new NodeList<>());
            } else if (nargs == 1) {
                rep = new MethodCallExpr(new NameExpr("ResponseEntity"), "ok", m.getArguments());
            } else {
                return false;
            }
            m.replace(rep);
            result.appliedChanges.add("Replaced ok(...) with ResponseEntity.ok(...)");
            return true;
        }
        if ("created".equals(name) && nargs == 1) {
            MethodCallExpr statusCall = new MethodCallExpr(new NameExpr("ResponseEntity"), "status",
                    new NodeList<>(new FieldAccessExpr(new NameExpr("HttpStatus"), "CREATED")));
            MethodCallExpr rep = new MethodCallExpr(statusCall, "body", m.getArguments());
            m.replace(rep);
            ensureImportForCurrentCu(m, "org.springframework.http.HttpStatus");
            result.appliedChanges.add("Replaced created(...) with ResponseEntity.status(CREATED).body(...)");
            return true;
        }
        if ("badRequest".equals(name)) {
            MethodCallExpr rep = nargs == 0
                    ? new MethodCallExpr(new MethodCallExpr(new NameExpr("ResponseEntity"), "badRequest"),
                    "build", new NodeList<>())
                    : new MethodCallExpr(new MethodCallExpr(new NameExpr("ResponseEntity"), "badRequest"),
                    "body", m.getArguments());
            m.replace(rep);
            result.appliedChanges.add("Replaced badRequest(...) with ResponseEntity.badRequest()...");
            return true;
        }
        if ("notFound".equals(name) && nargs == 0) {
            m.replace(new MethodCallExpr(new MethodCallExpr(new NameExpr("ResponseEntity"), "notFound"),
                    "build", new NodeList<>()));
            result.appliedChanges.add("Replaced notFound() with ResponseEntity.notFound().build()");
            return true;
        }
        if ("forbidden".equals(name) && nargs == 0) {
            m.replace(new MethodCallExpr(new MethodCallExpr(new NameExpr("ResponseEntity"), "status",
                    new NodeList<>(new FieldAccessExpr(new NameExpr("HttpStatus"), "FORBIDDEN"))),
                    "build", new NodeList<>()));
            ensureImportForCurrentCu(m, "org.springframework.http.HttpStatus");
            result.appliedChanges.add("Replaced forbidden() with ResponseEntity.status(FORBIDDEN).build()");
            return true;
        }
        if ("internalServerError".equals(name)) {
            MethodCallExpr rep = nargs == 0
                    ? new MethodCallExpr(new MethodCallExpr(new NameExpr("ResponseEntity"), "internalServerError"),
                    "build", new NodeList<>())
                    : new MethodCallExpr(new MethodCallExpr(new NameExpr("ResponseEntity"), "internalServerError"),
                    "body", m.getArguments());
            m.replace(rep);
            result.appliedChanges.add("Replaced internalServerError(...) with ResponseEntity...");
            return true;
        }
        if ("noContent".equals(name) && nargs == 0) {
            m.replace(new MethodCallExpr(new MethodCallExpr(new NameExpr("ResponseEntity"), "noContent"),
                    "build", new NodeList<>()));
            result.appliedChanges.add("Replaced noContent() with ResponseEntity.noContent().build()");
            return true;
        }
        return false;
    }

    private void ensureImportForCurrentCu(MethodCallExpr m, String fqcn) {
        m.findAncestor(CompilationUnit.class).ifPresent(cu -> ensureImport(cu, fqcn));
    }

    /**
     * True for unscoped calls (inherited from Play {@code Controller}) or {@code Results.*} static calls.
     */
    private boolean isPlayMvcHelperScope(MethodCallExpr m) {
        if (!m.getScope().isPresent()) {
            return true;
        }
        String s = m.getScope().get().toString();
        return "Results".equals(s) || s.endsWith(".Results");
    }

    private boolean importsPlayFramework(CompilationUnit cu) {
        return cu.getImports().stream()
                .anyMatch(i -> !i.isAsterisk() && i.getNameAsString().startsWith("play."));
    }

    /**
     * Tests and other non-controller Play sources: migrate {@code Result}, {@code Results.ok}/etc.,
     * {@code play.mvc.Http.Status.*} static constants, and {@code result.status()} to Spring MVC types.
     */
    private void applyPlayMvcNonControllerRewrites(ClassOrInterfaceDeclaration clazz, CompilationUnit cu,
                                                   Path testJavaSourcePath, TransformResult result) {
        rewritePlayHttpRequestMockitoPattern(clazz, cu, testJavaSourcePath, result);

        boolean hadPlayResultImport = cu.getImports().stream()
                .anyMatch(i -> "play.mvc.Result".equals(i.getNameAsString()));
        applyControllerHttpExecutionRewrites(cu, result);
        rewritePlayMvcHelperCallsInClass(clazz, cu, result);
        boolean typesChanged = rewriteAllPlayResultTypes(clazz, cu, result);
        if (typesChanged) {
            ensureImport(cu, "org.springframework.http.ResponseEntity");
            ensureImport(cu, "com.fasterxml.jackson.databind.JsonNode");
            result.appliedChanges.add("Replaced Play Result types with ResponseEntity<JsonNode> (non-controller)");
        }
        if (hadPlayResultImport || typesChanged) {
            if (rewritePlayResultStatusMethodCalls(cu)) {
                result.appliedChanges.add("Replaced Play Result.status() with ResponseEntity.getStatusCode().value()");
            }
        }
        if (rewritePlayHttpStatusStaticConstants(cu, result)) {
            result.appliedChanges.add("Replaced play.mvc.Http.Status constants with org.springframework.http.HttpStatus");
        }
    }

    /**
     * Rewrites typical Play test setup ({@code Http.Request} + {@code RequestBody} + {@code when(...body/asJson)})
     * to Spring controller calls ({@code JsonNode} + optional {@code HttpServletRequest}). Resolves which controller
     * methods take {@code (JsonNode, HttpServletRequest)} by parsing the sibling {@code src/main/java} class
     * inferred from the test file path ({@code *Test.java} / {@code *Tests.java}).
     */
    private void rewritePlayHttpRequestMockitoPattern(ClassOrInterfaceDeclaration clazz, CompilationUnit cu,
                                                      Path testJavaSourcePath, TransformResult result) {
        boolean usesPlayHttp = cu.getImports().stream()
                .anyMatch(i -> "play.mvc.Http".equals(i.getNameAsString()));
        if (!usesPlayHttp) {
            return;
        }
        Set<String> injectFields = collectInjectMocksFieldNames(clazz);
        if (injectFields.isEmpty()) {
            return;
        }
        Set<String> twoArgMethods = guessCompanionMainJavaController(testJavaSourcePath)
                .filter(Files::isRegularFile)
                .map(this::loadJsonBodyPlusServletMethodNames)
                .orElse(Collections.emptySet());

        boolean any = false;
        for (MethodDeclaration md : new ArrayList<>(clazz.getMethods())) {
            if (rewriteAndCleanupPlayHttpMockInMethod(md, injectFields, twoArgMethods)) {
                any = true;
            }
        }
        if (any) {
            ensureImport(cu, "javax.servlet.http.HttpServletRequest");
            result.appliedChanges.add(
                    "Rewrote Play Http.Request / RequestBody mocks to JsonNode (+ HttpServletRequest when required)");
        }
    }

    private Set<String> collectInjectMocksFieldNames(ClassOrInterfaceDeclaration clazz) {
        Set<String> names = new HashSet<>();
        for (FieldDeclaration fd : clazz.getFields()) {
            boolean injectMocks = fd.getAnnotations().stream()
                    .anyMatch(a -> "InjectMocks".equals(a.getNameAsString()));
            if (injectMocks) {
                fd.getVariables().forEach(v -> names.add(v.getNameAsString()));
            }
        }
        return names;
    }

    private Optional<Path> guessCompanionMainJavaController(Path testJavaFile) {
        if (testJavaFile == null) {
            return Optional.empty();
        }
        Path abs = testJavaFile.toAbsolutePath().normalize();
        String str = abs.toString().replace('\\', '/');
        String needle = "/src/test/java/";
        int idx = str.indexOf(needle);
        if (idx < 0) {
            return Optional.empty();
        }
        String prefix = str.substring(0, idx);
        String rel = str.substring(idx + needle.length());
        String controllerRel;
        if (rel.endsWith("Tests.java")) {
            controllerRel = rel.substring(0, rel.length() - "Tests.java".length()) + ".java";
        } else if (rel.endsWith("Test.java")) {
            controllerRel = rel.substring(0, rel.length() - "Test.java".length()) + ".java";
        } else {
            return Optional.empty();
        }
        return Optional.of(Paths.get(prefix, "src", "main", "java").resolve(controllerRel).normalize());
    }

    private Set<String> loadJsonBodyPlusServletMethodNames(Path controllerJava) {
        try {
            String src = new String(Files.readAllBytes(controllerJava));
            ParseResult<CompilationUnit> pr = parser.parse(src);
            if (!pr.isSuccessful() || !pr.getResult().isPresent()) {
                return Collections.emptySet();
            }
            CompilationUnit pcu = pr.getResult().get();
            Optional<ClassOrInterfaceDeclaration> co = pcu.findFirst(ClassOrInterfaceDeclaration.class);
            if (!co.isPresent()) {
                return Collections.emptySet();
            }
            Set<String> out = new HashSet<>();
            for (MethodDeclaration md : co.get().getMethods()) {
                if (!md.isPublic()) {
                    continue;
                }
                NodeList<Parameter> ps = md.getParameters();
                if (ps.size() < 2) {
                    continue;
                }
                if (!typeLooksLikeJsonNode(ps.get(0).getType())) {
                    continue;
                }
                boolean servlet = false;
                for (int i = 1; i < ps.size(); i++) {
                    if (typeLooksLikeHttpServletRequest(ps.get(i).getType())) {
                        servlet = true;
                        break;
                    }
                }
                if (servlet) {
                    out.add(md.getNameAsString());
                }
            }
            return out;
        } catch (IOException e) {
            return Collections.emptySet();
        }
    }

    private boolean typeLooksLikeJsonNode(Type t) {
        return t.asString().replace(" ", "").contains("JsonNode");
    }

    private boolean typeLooksLikeHttpServletRequest(Type t) {
        return t.asString().replace(" ", "").contains("HttpServletRequest");
    }

    private boolean rewriteAndCleanupPlayHttpMockInMethod(MethodDeclaration md, Set<String> injectFields,
                                                          Set<String> twoArgMethods) {
        Optional<String> reqVar = extractPlayHttpRequestMockVarName(md);
        if (!reqVar.isPresent()) {
            return false;
        }
        Optional<String> rbVar = extractPlayHttpRequestBodyMockVarName(md);
        if (!rbVar.isPresent()) {
            return false;
        }
        Optional<String> jsonNode = extractJsonNodeNameFromAsJsonThenReturn(md, rbVar.get());
        if (!jsonNode.isPresent()) {
            return false;
        }

        boolean needsServlet = false;
        for (MethodCallExpr m : new ArrayList<>(md.findAll(MethodCallExpr.class))) {
            if (m.getArguments().size() != 1 || !m.getArgument(0).isNameExpr()) {
                continue;
            }
            if (!reqVar.get().equals(m.getArgument(0).asNameExpr().getNameAsString())) {
                continue;
            }
            Optional<NameExpr> sc = m.getScope().filter(NameExpr.class::isInstance).map(NameExpr.class::cast);
            if (!sc.isPresent() || !injectFields.contains(sc.get().getNameAsString())) {
                continue;
            }
            String methodName = m.getNameAsString();
            if (twoArgMethods.contains(methodName)) {
                needsServlet = true;
                NodeList<Expression> nargs = new NodeList<>();
                nargs.add(new NameExpr(jsonNode.get()));
                nargs.add(new NameExpr(reqVar.get()));
                m.setArguments(nargs);
            } else {
                m.setArguments(new NodeList<>(new NameExpr(jsonNode.get())));
            }
        }

        removePlayHttpMockWhenStatements(md, reqVar.get(), rbVar.get());
        removeLocalVariableDeclarationStatement(md, rbVar.get());

        if (needsServlet) {
            convertPlayHttpRequestMockVarToServlet(md, reqVar.get());
        } else {
            removeLocalVariableDeclarationStatement(md, reqVar.get());
        }
        return true;
    }

    private Optional<String> extractPlayHttpRequestMockVarName(MethodDeclaration md) {
        for (VariableDeclarator v : md.findAll(VariableDeclarator.class)) {
            if (!isPlayHttpRequestType(v.getType())) {
                continue;
            }
            if (!v.getInitializer().isPresent() || !v.getInitializer().get().isMethodCallExpr()) {
                continue;
            }
            MethodCallExpr mc = v.getInitializer().get().asMethodCallExpr();
            if (!"mock".equals(mc.getNameAsString()) || mc.getArguments().size() != 1) {
                continue;
            }
            if (!mc.getArgument(0).isClassExpr()) {
                continue;
            }
            String mocked = mc.getArgument(0).asClassExpr().getType().asString().replace(" ", "");
            if (mocked.contains("Http.Request")) {
                return Optional.of(v.getNameAsString());
            }
        }
        return Optional.empty();
    }

    private Optional<String> extractPlayHttpRequestBodyMockVarName(MethodDeclaration md) {
        for (VariableDeclarator v : md.findAll(VariableDeclarator.class)) {
            if (!isPlayHttpRequestBodyType(v.getType())) {
                continue;
            }
            if (!v.getInitializer().isPresent() || !v.getInitializer().get().isMethodCallExpr()) {
                continue;
            }
            MethodCallExpr mc = v.getInitializer().get().asMethodCallExpr();
            if (!"mock".equals(mc.getNameAsString()) || mc.getArguments().size() != 1) {
                continue;
            }
            if (!mc.getArgument(0).isClassExpr()) {
                continue;
            }
            String mocked = mc.getArgument(0).asClassExpr().getType().asString().replace(" ", "");
            if (mocked.contains("RequestBody")) {
                return Optional.of(v.getNameAsString());
            }
        }
        return Optional.empty();
    }

    private boolean isPlayHttpRequestBodyType(Type t) {
        String s = t.asString().replace(" ", "");
        return s.contains("RequestBody") && s.contains("Http");
    }

    private Optional<String> extractJsonNodeNameFromAsJsonThenReturn(MethodDeclaration md, String rbVar) {
        for (MethodCallExpr m : md.findAll(MethodCallExpr.class)) {
            if (!"thenReturn".equals(m.getNameAsString()) || m.getArguments().size() != 1) {
                continue;
            }
            Optional<MethodCallExpr> whenMc = m.getScope()
                    .filter(MethodCallExpr.class::isInstance)
                    .map(MethodCallExpr.class::cast);
            if (!whenMc.isPresent() || !"when".equals(whenMc.get().getNameAsString())
                    || whenMc.get().getArguments().size() != 1) {
                continue;
            }
            Expression whenArg = whenMc.get().getArgument(0);
            if (!whenArg.isMethodCallExpr()) {
                continue;
            }
            MethodCallExpr inner = whenArg.asMethodCallExpr();
            if (!"asJson".equals(inner.getNameAsString())) {
                continue;
            }
            if (!inner.getScope().filter(NameExpr.class::isInstance).map(NameExpr.class::cast)
                    .map(n -> rbVar.equals(n.getNameAsString())).orElse(false)) {
                continue;
            }
            Expression ret = m.getArgument(0);
            if (!ret.isNameExpr()) {
                continue;
            }
            return Optional.of(ret.asNameExpr().getNameAsString());
        }
        return Optional.empty();
    }

    private void removePlayHttpMockWhenStatements(MethodDeclaration md, String requestVar, String rbVar) {
        BlockStmt block = md.getBody().orElse(null);
        if (block == null) {
            return;
        }
        List<Statement> remove = new ArrayList<>();
        for (Statement st : block.getStatements()) {
            if (!st.isExpressionStmt()) {
                continue;
            }
            Expression e = st.asExpressionStmt().getExpression();
            if (isWhenThenReturnForPlayRequestOrBody(e, requestVar, rbVar)) {
                remove.add(st);
            }
        }
        block.getStatements().removeAll(remove);
    }

    private boolean isWhenThenReturnForPlayRequestOrBody(Expression e, String requestVar, String rbVar) {
        if (!e.isMethodCallExpr()) {
            return false;
        }
        MethodCallExpr top = e.asMethodCallExpr();
        if (!"thenReturn".equals(top.getNameAsString())) {
            return false;
        }
        Optional<MethodCallExpr> whenMc = top.getScope()
                .filter(MethodCallExpr.class::isInstance)
                .map(MethodCallExpr.class::cast);
        if (!whenMc.isPresent() || !"when".equals(whenMc.get().getNameAsString())
                || whenMc.get().getArguments().size() != 1) {
            return false;
        }
        Expression whenArg = whenMc.get().getArgument(0);
        if (!whenArg.isMethodCallExpr()) {
            return false;
        }
        MethodCallExpr inner = whenArg.asMethodCallExpr();
        if ("body".equals(inner.getNameAsString())) {
            return inner.getScope().filter(NameExpr.class::isInstance).map(NameExpr.class::cast)
                    .map(n -> requestVar.equals(n.getNameAsString())).orElse(false);
        }
        if ("asJson".equals(inner.getNameAsString())) {
            return inner.getScope().filter(NameExpr.class::isInstance).map(NameExpr.class::cast)
                    .map(n -> rbVar.equals(n.getNameAsString())).orElse(false);
        }
        return false;
    }

    private void removeLocalVariableDeclarationStatement(MethodDeclaration md, String varName) {
        BlockStmt block = md.getBody().orElse(null);
        if (block == null) {
            return;
        }
        List<Statement> remove = new ArrayList<>();
        for (Statement st : block.getStatements()) {
            if (!st.isExpressionStmt()) {
                continue;
            }
            Expression ex = st.asExpressionStmt().getExpression();
            if (!ex.isVariableDeclarationExpr()) {
                continue;
            }
            for (VariableDeclarator v : ex.asVariableDeclarationExpr().getVariables()) {
                if (varName.equals(v.getNameAsString())) {
                    remove.add(st);
                    break;
                }
            }
        }
        block.getStatements().removeAll(remove);
    }

    private void convertPlayHttpRequestMockVarToServlet(MethodDeclaration md, String varName) {
        BlockStmt block = md.getBody().orElse(null);
        if (block == null) {
            return;
        }
        for (Statement st : block.getStatements()) {
            if (!st.isExpressionStmt()) {
                continue;
            }
            Expression ex = st.asExpressionStmt().getExpression();
            if (!ex.isVariableDeclarationExpr()) {
                continue;
            }
            for (VariableDeclarator v : ex.asVariableDeclarationExpr().getVariables()) {
                if (!varName.equals(v.getNameAsString())) {
                    continue;
                }
                ClassOrInterfaceType servlet = new ClassOrInterfaceType(null, "HttpServletRequest");
                v.setType(servlet);
                v.setInitializer(new MethodCallExpr(null, "mock", new NodeList<>(new ClassExpr(servlet))));
            }
        }
    }

    private void rewritePlayMvcHelperCallsInClass(ClassOrInterfaceDeclaration clazz, CompilationUnit cu,
                                                  TransformResult result) {
        List<MethodDeclaration> methods = new ArrayList<>(clazz.getMethods());
        for (MethodDeclaration md : methods) {
            List<MethodCallExpr> calls = new ArrayList<>(md.findAll(MethodCallExpr.class));
            for (MethodCallExpr m : calls) {
                rewritePlayResultFactoryCall(m, result);
            }
        }
    }

    /**
     * @return true if any type was changed
     */
    private boolean rewriteAllPlayResultTypes(ClassOrInterfaceDeclaration clazz, CompilationUnit cu,
                                              TransformResult result) {
        boolean changed = false;
        for (FieldDeclaration fd : clazz.getFields()) {
            for (VariableDeclarator v : fd.getVariables()) {
                if (replacePlayResultInType(v.getType())) {
                    changed = true;
                }
            }
        }
        for (ConstructorDeclaration cd : clazz.getConstructors()) {
            for (Parameter p : cd.getParameters()) {
                if (replacePlayResultInType(p.getType())) {
                    changed = true;
                }
            }
        }
        for (MethodDeclaration md : clazz.getMethods()) {
            if (replacePlayResultInType(md.getType())) {
                changed = true;
            }
            for (Parameter p : md.getParameters()) {
                if (replacePlayResultInType(p.getType())) {
                    changed = true;
                }
            }
        }
        for (VariableDeclarator vd : clazz.findAll(VariableDeclarator.class)) {
            if (replacePlayResultInType(vd.getType())) {
                changed = true;
            }
        }
        return changed;
    }

    private boolean rewritePlayResultStatusMethodCalls(CompilationUnit cu) {
        boolean changed = false;
        List<MethodCallExpr> calls = new ArrayList<>(cu.findAll(MethodCallExpr.class));
        for (MethodCallExpr m : calls) {
            if (!"status".equals(m.getNameAsString()) || !m.getArguments().isEmpty()) {
                continue;
            }
            if (!m.getScope().isPresent()) {
                continue;
            }
            Expression scope = m.getScope().get();
            MethodCallExpr inner = new MethodCallExpr(scope.clone(), "getStatusCode", new NodeList<>());
            m.replace(new MethodCallExpr(inner, "value", new NodeList<>()));
            changed = true;
        }
        return changed;
    }

    /**
     * Rewrites bare names imported from {@code import static play.mvc.Http.Status.*} to {@code HttpStatus.*.value()}.
     */
    private boolean rewritePlayHttpStatusStaticConstants(CompilationUnit cu, TransformResult result) {
        Set<String> constants = new LinkedHashSet<>();
        for (ImportDeclaration imp : new ArrayList<>(cu.getImports())) {
            if (!imp.isStatic()) {
                continue;
            }
            String n = imp.getNameAsString();
            if (!n.startsWith("play.mvc.Http.Status.")) {
                continue;
            }
            String c = n.substring("play.mvc.Http.Status.".length());
            if (!c.isEmpty()) {
                constants.add(c);
            }
        }
        if (constants.isEmpty()) {
            return false;
        }
        ensureImport(cu, "org.springframework.http.HttpStatus");
        for (String c : constants) {
            for (NameExpr ne : new ArrayList<>(cu.findAll(NameExpr.class))) {
                if (!c.equals(ne.getNameAsString())) {
                    continue;
                }
                MethodCallExpr valueCall = new MethodCallExpr(
                        new FieldAccessExpr(new NameExpr("HttpStatus"), c),
                        "value",
                        new NodeList<>());
                ne.replace(valueCall);
            }
        }
        return true;
    }

    /**
     * Drops {@code play.*} imports after rewrites. Keeps imports still required for unmigrated Play APIs
     * (e.g. {@code Http.Request} mocks, {@code ApplicationLifecycle}, embedded HTTP test servers).
     */
    private void removePlayMvcImports(CompilationUnit cu, TransformResult result) {
        int before = cu.getImports().size();
        cu.getImports().removeIf(i -> {
            if (i.isAsterisk()) {
                return false;
            }
            String n = i.getNameAsString();
            if (!n.startsWith("play.")) {
                return false;
            }
            return !shouldRetainPlayImport(cu, n);
        });
        int removed = before - cu.getImports().size();
        if (removed > 0) {
            result.appliedChanges.add("Removed " + removed + " Play framework import(s)");
        }
    }

    private boolean shouldRetainPlayImport(CompilationUnit cu, String importName) {
        if ("play.mvc.Http".equals(importName) && referencesPlayHttpApi(cu)) {
            return true;
        }
        if (importName.startsWith("play.inject.") && cu.toString().contains("ApplicationLifecycle")) {
            return true;
        }
        if (importName.startsWith("play.routing.") || importName.startsWith("play.server.")) {
            int dot = importName.lastIndexOf('.');
            if (dot >= 0 && dot < importName.length() - 1) {
                String simple = importName.substring(dot + 1);
                if (cu.toString().contains(simple)) {
                    return true;
                }
            }
        }
        return false;
    }

    private boolean referencesPlayHttpApi(CompilationUnit cu) {
        String s = cu.toString();
        return s.contains("Http.Request")
                || s.contains("Http.Headers")
                || s.contains("Http.Context")
                || s.contains("Http.Session")
                || s.contains("Http.Cookies")
                || s.contains("Http.Cookie")
                || s.contains("Http.MultipartFormData");
    }

    private void ensureImport(CompilationUnit cu, String fqcn) {
        boolean already = cu.getImports().stream().anyMatch(i -> i.getNameAsString().equals(fqcn));
        if (!already) {
            cu.addImport(new ImportDeclaration(fqcn, false, false));
        }
    }

    public void writeReport(TransformResult result, Path reportPath) throws IOException {
        Files.createDirectories(reportPath.getParent());
        objectMapper.writerWithDefaultPrettyPrinter().writeValue(reportPath.toFile(), result);
    }
}
