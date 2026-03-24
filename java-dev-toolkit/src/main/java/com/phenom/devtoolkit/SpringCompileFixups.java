package com.phenom.devtoolkit;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.CastExpr;
import com.github.javaparser.ast.expr.ConditionalExpr;
import com.github.javaparser.ast.expr.EnclosedExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.InstanceOfExpr;
import com.github.javaparser.ast.expr.MarkerAnnotationExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.expr.StringLiteralExpr;
import com.github.javaparser.ast.stmt.ReturnStmt;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.Type;
import com.github.javaparser.ast.ImportDeclaration;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Post-transform fixes that address common Spring 6 / Jackson / Neo4j compile failures after Play migration.
 */
final class SpringCompileFixups {

    private static final Set<String> TRIM_PACKAGE_SUFFIX = new HashSet<>(Arrays.asList(
            "service", "services", "core", "controller", "controllers", "db",
            "repository", "repositories", "model", "models", "assets", "events", "config"
    ));

    private static final Set<String> BARE_HTTP_STATUS_NAMES = new HashSet<>(Arrays.asList(
            "UNAUTHORIZED", "BAD_REQUEST", "NOT_FOUND", "FORBIDDEN", "INTERNAL_SERVER_ERROR",
            "CONFLICT", "NOT_ACCEPTABLE", "UNSUPPORTED_MEDIA_TYPE", "METHOD_NOT_ALLOWED",
            "TOO_MANY_REQUESTS", "SERVICE_UNAVAILABLE", "GATEWAY_TIMEOUT"
    ));

    private SpringCompileFixups() {
    }

    /**
     * Run after main Play→Spring AST rewrites, before pruning imports.
     */
    static void apply(
            CompilationUnit cu,
            ClassOrInterfaceDeclaration clazz,
            LayerDetector.Layer layer,
            PlayToSpringTransformer.TransformResult result) {

        if (rewriteObjectMapperReadTree(cu, result)) {
            String utilsPkg = jacksonJsonUtilsPackage(cu);
            result.jacksonJsonUtilsPackage = utilsPkg;
        }
        if (rewriteGetReasonPhraseAfterGetStatusCode(cu, result)) {
            ensureImport(cu, "org.springframework.http.HttpStatus");
        }
        if (rewriteNeo4jServerAddressResolverListToSet(cu, result)) {
            ensureImport(cu, "java.util.Set");
            ensureImport(cu, "java.util.LinkedHashSet");
        }
        if (layer == LayerDetector.Layer.CONTROLLER && rewriteCompletableFutureSupplyAsyncJsonEntity(cu, result)) {
            // types only; imports usually already present
        }
        if (rewriteGetErrorResultBareStatusToHttpStatusValue(cu, result)) {
            ensureImport(cu, "org.springframework.http.HttpStatus");
        }
        if (rewriteServletGetHeadersStringGet(cu, result)) {
            ensureImport(cu, "java.util.Optional");
        }
        if (layer == LayerDetector.Layer.CONTROLLER && rewriteResponseEntityOkStringLiteral(cu, clazz, result)) {
            // uses objectMapper.valueToTree
        }
        if (rewriteAutowiredRequiredFalseConstructorParamsToOptional(cu, clazz, result)) {
            ensureImport(cu, "java.util.Optional");
            ensureImport(cu, "org.springframework.beans.factory.annotation.Autowired");
        }
    }

    static String inferJacksonJsonFqcn(CompilationUnit cu) {
        String pkg = cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse("com.example.app");
        if (pkg.endsWith(".utils")) {
            return pkg + ".JacksonJson";
        }
        String[] parts = pkg.split("\\.");
        List<String> p = new ArrayList<>(Arrays.asList(parts));
        while (p.size() > 2 && TRIM_PACKAGE_SUFFIX.contains(p.get(p.size() - 1))) {
            p.remove(p.size() - 1);
        }
        return String.join(".", p) + ".utils.JacksonJson";
    }

    private static String jacksonJsonUtilsPackage(CompilationUnit cu) {
        String fqcn = inferJacksonJsonFqcn(cu);
        int dot = fqcn.lastIndexOf('.');
        return dot > 0 ? fqcn.substring(0, dot) : "utils";
    }

    private static boolean rewriteObjectMapperReadTree(CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        boolean any = false;
        List<MethodCallExpr> calls = new ArrayList<>(cu.findAll(MethodCallExpr.class));
        for (MethodCallExpr m : calls) {
            if (!"readTree".equals(m.getNameAsString()) || m.getArguments().size() != 1) {
                continue;
            }
            Optional<Expression> scope = m.getScope();
            if (!scope.isPresent() || !scope.get().isNameExpr()) {
                continue;
            }
            if (!"objectMapper".equals(scope.get().asNameExpr().getNameAsString())) {
                continue;
            }
            if (m.getScope().map(s -> "JacksonJson".equals(s.toString()) || s.toString().endsWith(".JacksonJson"))
                    .orElse(false)) {
                continue;
            }
            String fqcn = inferJacksonJsonFqcn(cu);
            String simple = fqcn.substring(fqcn.lastIndexOf('.') + 1);
            MethodCallExpr rep = new MethodCallExpr(new NameExpr(simple), "readTree",
                    new NodeList<>(new NameExpr("objectMapper"), m.getArgument(0)));
            m.replace(rep);
            ensureImport(cu, fqcn);
            any = true;
        }
        if (any) {
            result.appliedChanges.add("Replaced objectMapper.readTree with JacksonJson.readTree (unchecked JSON parse)");
        }
        return any;
    }

    private static boolean rewriteGetReasonPhraseAfterGetStatusCode(
            CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        boolean any = false;
        List<MethodCallExpr> calls = new ArrayList<>(cu.findAll(MethodCallExpr.class));
        for (MethodCallExpr m : calls) {
            if (!"getReasonPhrase".equals(m.getNameAsString()) || !m.getArguments().isEmpty()) {
                continue;
            }
            Optional<Expression> sc = m.getScope();
            if (!sc.isPresent() || !sc.get().isMethodCallExpr()) {
                continue;
            }
            MethodCallExpr statusCall = sc.get().asMethodCallExpr();
            if (!"getStatusCode".equals(statusCall.getNameAsString())) {
                continue;
            }
            Expression codeExpr = statusCall;
            ClassOrInterfaceType httpStatusType = new ClassOrInterfaceType(null, "HttpStatus");
            InstanceOfExpr cond = new InstanceOfExpr(codeExpr.clone(), httpStatusType);
            CastExpr casted = new CastExpr(httpStatusType, codeExpr.clone());
            MethodCallExpr thenPart = new MethodCallExpr(casted, "getReasonPhrase");
            MethodCallExpr elsePart = new MethodCallExpr(codeExpr.clone(), "toString");
            ConditionalExpr ce = new ConditionalExpr(
                    new EnclosedExpr(cond),
                    new EnclosedExpr(thenPart),
                    new EnclosedExpr(elsePart));
            m.replace(ce);
            any = true;
        }
        if (any) {
            result.appliedChanges.add("Replaced getStatusCode().getReasonPhrase() with HttpStatus-safe phrase/toString");
        }
        return any;
    }

    private static boolean rewriteNeo4jServerAddressResolverListToSet(
            CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        if (!cu.toString().contains("ServerAddress")) {
            return false;
        }
        boolean any = false;
        for (MethodDeclaration md : new ArrayList<>(cu.findAll(MethodDeclaration.class))) {
            if (!"resolve".equals(md.getNameAsString())) {
                continue;
            }
            String ret = md.getType().asString().replace(" ", "");
            if (!ret.contains("List<ServerAddress>")) {
                continue;
            }
            ClassOrInterfaceType elem = new ClassOrInterfaceType(null, "ServerAddress");
            ClassOrInterfaceType setType = new ClassOrInterfaceType(null, "Set");
            setType.setTypeArguments(new NodeList<>(elem));
            md.setType(setType);
            md.findAll(ReturnStmt.class).forEach(rs -> rs.getExpression().ifPresent(e -> {
                if (e instanceof ObjectCreationExpr) {
                    ObjectCreationExpr oc = (ObjectCreationExpr) e;
                    String t = oc.getType().getNameAsString();
                    if ("ArrayList".equals(t) || "LinkedList".equals(t)) {
                        ObjectCreationExpr rep = new ObjectCreationExpr(null,
                                new ClassOrInterfaceType(null, "LinkedHashSet"),
                                new NodeList<>(),
                                oc.getArguments(),
                                new NodeList<>());
                        e.replace(rep);
                    }
                }
            }));
            any = true;
        }
        if (any) {
            result.appliedChanges.add("Neo4j ServerAddressResolver: List<ServerAddress> → Set + LinkedHashSet");
        }
        return any;
    }

    private static boolean methodReturnsCompletableFutureResponseEntityJson(MethodDeclaration md) {
        String t = md.getType().asString().replace(" ", "");
        return t.contains("CompletableFuture") && t.contains("ResponseEntity") && t.contains("JsonNode");
    }

    private static boolean rewriteCompletableFutureSupplyAsyncJsonEntity(
            CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        boolean any = false;
        for (MethodDeclaration md : cu.findAll(MethodDeclaration.class)) {
            if (!methodReturnsCompletableFutureResponseEntityJson(md)) {
                continue;
            }
            for (MethodCallExpr m : new ArrayList<>(md.findAll(MethodCallExpr.class))) {
                if (!"supplyAsync".equals(m.getNameAsString())) {
                    continue;
                }
                if (m.getTypeArguments().isPresent() && !m.getTypeArguments().get().isEmpty()) {
                    continue;
                }
                boolean staticCf = !m.getScope().isPresent()
                        || "CompletableFuture".equals(m.getScope().get().toString())
                        || m.getScope().get().toString().endsWith(".CompletableFuture");
                if (!staticCf) {
                    continue;
                }
                ClassOrInterfaceType inner = new ClassOrInterfaceType(null, "ResponseEntity");
                inner.setTypeArguments(new NodeList<>(new ClassOrInterfaceType(null, "JsonNode")));
                m.setTypeArguments(new NodeList<>(inner));
                any = true;
            }
        }
        if (any) {
            result.appliedChanges.add("Added CompletableFuture.<ResponseEntity<JsonNode>>supplyAsync type args");
        }
        return any;
    }

    private static boolean rewriteGetErrorResultBareStatusToHttpStatusValue(
            CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        boolean any = false;
        for (MethodCallExpr m : new ArrayList<>(cu.findAll(MethodCallExpr.class))) {
            if (!"getErrorResult".equals(m.getNameAsString()) || m.getArguments().isEmpty()) {
                continue;
            }
            Expression first = m.getArgument(0);
            if (!first.isNameExpr()) {
                continue;
            }
            String name = first.asNameExpr().getNameAsString();
            if (!BARE_HTTP_STATUS_NAMES.contains(name)) {
                continue;
            }
            MethodCallExpr valueCall = new MethodCallExpr(
                    new FieldAccessExpr(new NameExpr("HttpStatus"), name),
                    "value");
            m.setArgument(0, valueCall);
            any = true;
        }
        if (any) {
            result.appliedChanges.add("getErrorResult: bare status name → HttpStatus.*.value()");
        }
        return any;
    }

    private static boolean rewriteServletGetHeadersStringGet(
            CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        boolean any = false;
        List<MethodCallExpr> calls = new ArrayList<>(cu.findAll(MethodCallExpr.class));
        for (MethodCallExpr outer : calls) {
            if (!"get".equals(outer.getNameAsString()) || outer.getArguments().size() != 1) {
                continue;
            }
            if (!outer.getArgument(0).isStringLiteralExpr()) {
                continue;
            }
            Optional<Expression> osc = outer.getScope();
            if (!osc.isPresent() || !osc.get().isMethodCallExpr()) {
                continue;
            }
            MethodCallExpr headersCall = osc.get().asMethodCallExpr();
            if (!"getHeaders".equals(headersCall.getNameAsString()) || !headersCall.getArguments().isEmpty()) {
                continue;
            }
            Optional<Expression> recv = headersCall.getScope();
            if (!recv.isPresent()) {
                continue;
            }
            String headerName = outer.getArgument(0).asStringLiteralExpr().getValue();
            MethodCallExpr getHeader = new MethodCallExpr(recv.get().clone(), "getHeader",
                    new NodeList<>(new StringLiteralExpr(headerName)));
            MethodCallExpr wrapped = new MethodCallExpr(new NameExpr("Optional"), "ofNullable",
                    new NodeList<>(getHeader));
            outer.replace(wrapped);
            any = true;
        }
        if (any) {
            result.appliedChanges.add("request.getHeaders().get(\"…\") → Optional.ofNullable(request.getHeader(\"…\"))");
        }
        return any;
    }

    private static boolean hasObjectMapperField(ClassOrInterfaceDeclaration clazz) {
        return clazz.getFields().stream()
                .flatMap(f -> f.getVariables().stream())
                .anyMatch(v -> "objectMapper".equals(v.getNameAsString()));
    }

    private static boolean rewriteResponseEntityOkStringLiteral(
            CompilationUnit cu,
            ClassOrInterfaceDeclaration clazz,
            PlayToSpringTransformer.TransformResult result) {
        if (!hasObjectMapperField(clazz)) {
            return false;
        }
        boolean any = false;
        for (MethodDeclaration md : clazz.getMethods()) {
            String ret = md.getType().asString().replace(" ", "");
            if (!ret.contains("ResponseEntity<JsonNode>") && !ret.contains("CompletableFuture<ResponseEntity<JsonNode>>")) {
                continue;
            }
            for (MethodCallExpr m : new ArrayList<>(md.findAll(MethodCallExpr.class))) {
                if (!"ok".equals(m.getNameAsString()) || m.getArguments().size() != 1) {
                    continue;
                }
                if (!m.getScope().isPresent() || !"ResponseEntity".equals(m.getScope().get().toString())) {
                    continue;
                }
                if (!m.getArgument(0).isStringLiteralExpr()) {
                    continue;
                }
                StringLiteralExpr lit = m.getArgument(0).asStringLiteralExpr();
                MethodCallExpr vt = new MethodCallExpr(new NameExpr("objectMapper"), "valueToTree",
                        new NodeList<>(lit.clone()));
                m.setArgument(0, vt);
                any = true;
            }
        }
        if (any) {
            result.appliedChanges.add("ResponseEntity.ok(\"…\") → ok(objectMapper.valueToTree(\"…\")) for JsonNode return");
        }
        return any;
    }

    private static void ensureImport(CompilationUnit cu, String fqcn) {
        boolean already = cu.getImports().stream().anyMatch(i -> i.getNameAsString().equals(fqcn));
        if (!already) {
            cu.addImport(new ImportDeclaration(fqcn, false, false));
        }
    }

    /**
     * {@code @Autowired(required=false)} on a constructor parameter does not mean “optional dependency” in Spring
     * (unlike fields/setters). Rewrite to {@code Optional<T>} and ensure the constructor has {@code @Autowired}.
     */
    private static boolean rewriteAutowiredRequiredFalseConstructorParamsToOptional(
            CompilationUnit cu,
            ClassOrInterfaceDeclaration clazz,
            PlayToSpringTransformer.TransformResult result) {
        boolean any = false;
        for (ConstructorDeclaration cd : clazz.getConstructors()) {
            boolean touched = false;
            for (Parameter param : cd.getParameters()) {
                List<AnnotationExpr> toStrip = new ArrayList<>();
                for (AnnotationExpr ann : param.getAnnotations()) {
                    if (isAutowiredRequiredFalse(ann)) {
                        toStrip.add(ann);
                    }
                }
                if (toStrip.isEmpty()) {
                    continue;
                }
                for (AnnotationExpr ann : toStrip) {
                    param.getAnnotations().remove(ann);
                }
                if (!isTypeAlreadyJavaUtilOptional(param.getType())) {
                    Type inner = param.getType().clone();
                    ClassOrInterfaceType opt = new ClassOrInterfaceType(null, "Optional");
                    opt.setTypeArguments(new NodeList<>(inner));
                    param.setType(opt);
                }
                touched = true;
                any = true;
            }
            if (touched && !constructorHasAutowiredAnnotation(cd)) {
                cd.addAnnotation(new MarkerAnnotationExpr("Autowired"));
            }
        }
        if (any) {
            result.appliedChanges.add(
                    "Constructor @Autowired(required=false) on param → Optional<…> (Spring Framework injection semantics)");
        }
        return any;
    }

    private static boolean constructorHasAutowiredAnnotation(ConstructorDeclaration cd) {
        return cd.getAnnotations().stream().anyMatch(SpringCompileFixups::isAutowiredAnnotationName);
    }

    private static boolean isAutowiredAnnotationName(AnnotationExpr a) {
        String n = a.getNameAsString();
        return "Autowired".equals(n) || n.endsWith(".Autowired");
    }

    private static boolean isAutowiredRequiredFalse(AnnotationExpr ann) {
        if (!isAutowiredAnnotationName(ann) || !(ann instanceof NormalAnnotationExpr)) {
            return false;
        }
        for (MemberValuePair pair : ((NormalAnnotationExpr) ann).getPairs()) {
            if ("required".equals(pair.getNameAsString())
                    && pair.getValue().isBooleanLiteralExpr()
                    && !pair.getValue().asBooleanLiteralExpr().getValue()) {
                return true;
            }
        }
        return false;
    }

    private static boolean isTypeAlreadyJavaUtilOptional(Type t) {
        if (!(t instanceof ClassOrInterfaceType)) {
            return false;
        }
        ClassOrInterfaceType c = (ClassOrInterfaceType) t;
        return "Optional".equals(c.getNameAsString()) && c.getTypeArguments().isPresent();
    }
}
