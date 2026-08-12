package com.phenom.devtoolkit;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Modifier;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.MarkerAnnotationExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.MethodReferenceExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.SimpleName;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import com.github.javaparser.ast.stmt.ReturnStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.Type;

import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Rewrites Play {@code WSClient}/{@code WSResponse} toward Spring {@code RestTemplate}/{@code ResponseEntity},
 * and {@code ApplicationLifecycle#addStopHook} toward {@code @PreDestroy}.
 */
final class PlayWsSpringRewriter {

    private final JavaParser parser;
    private final AtomicInteger syntheticId = new AtomicInteger();

    PlayWsSpringRewriter(JavaParser parser) {
        this.parser = parser;
    }

    /**
     * @return true if generated code references {@code objectMapper.readTree} and a field may be needed
     */
    boolean apply(ClassOrInterfaceDeclaration clazz, CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        Set<String> wsResponseNames = collectNamesTypedWsResponse(cu);
        Set<MethodCallExpr> skip = Collections.newSetFromMap(new IdentityHashMap<>());
        boolean changed = false;
        boolean needsObjectMapper = false;
        if (rewriteWsBlockingChains(cu, result, skip)) {
            changed = true;
        }
        if (rewriteWsThenApplyChains(cu, result, skip)) {
            changed = true;
            needsObjectMapper = true;
        }
        if (replaceWsResponseMethodCalls(cu, wsResponseNames, result)) {
            changed = true;
        }
        if (rewriteApplicationLifecycle(clazz, cu, result)) {
            changed = true;
        }
        if (replaceWsTypesAndFields(cu, result)) {
            changed = true;
        }
        if (changed) {
            ensureImports(cu);
            stripPlayWsImports(cu, result);
        }
        return needsObjectMapper;
    }

    private void ensureImports(CompilationUnit cu) {
        addImportIfMissing(cu, "org.springframework.web.client.RestTemplate");
        addImportIfMissing(cu, "org.springframework.http.HttpEntity");
        addImportIfMissing(cu, "org.springframework.http.HttpHeaders");
        addImportIfMissing(cu, "org.springframework.http.HttpMethod");
        addImportIfMissing(cu, "org.springframework.http.ResponseEntity");
        addImportIfMissing(cu, "javax.annotation.PreDestroy");
    }

    private void addImportIfMissing(CompilationUnit cu, String fqcn) {
        boolean has = cu.getImports().stream().anyMatch(i -> fqcn.equals(i.getNameAsString()));
        if (!has) {
            cu.addImport(fqcn, false, false);
        }
    }

    private void stripPlayWsImports(CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        int before = cu.getImports().size();
        cu.getImports().removeIf(i -> {
            String n = i.getNameAsString();
            return "play.libs.ws.WSClient".equals(n)
                    || "play.libs.ws.WSResponse".equals(n)
                    || "play.inject.ApplicationLifecycle".equals(n);
        });
        int removed = before - cu.getImports().size();
        if (removed > 0) {
            result.appliedChanges.add("Removed " + removed + " Play WS / ApplicationLifecycle import(s)");
        }
    }

    private boolean rewriteWsBlockingChains(CompilationUnit cu, PlayToSpringTransformer.TransformResult result,
                                            Set<MethodCallExpr> skip) {
        boolean any = false;
        for (MethodCallExpr getCall : new ArrayList<>(cu.findAll(MethodCallExpr.class))) {
            if (skip.contains(getCall)) {
                continue;
            }
            if (!isBareGet(getCall)) {
                continue;
            }
            Optional<MethodCallExpr> toCf = getCall.getScope().filter(MethodCallExpr.class::isInstance).map(MethodCallExpr.class::cast);
            if (!toCf.isPresent() || !"toCompletableFuture".equals(toCf.get().getNameAsString())
                    || !toCf.get().getArguments().isEmpty()) {
                continue;
            }
            MethodCallExpr terminal = toCf.get().getScope().filter(MethodCallExpr.class::isInstance).map(MethodCallExpr.class::cast).orElse(null);
            if (terminal == null || "thenApply".equals(terminal.getNameAsString())) {
                continue;
            }
            Optional<ParsedWsChain> chain = parseWsRequestChain(terminal);
            if (!chain.isPresent()) {
                continue;
            }
            ParsedWsChain c = chain.get();
            String hdrVar = "playWsHeaders" + syntheticId.incrementAndGet();
            List<Statement> prelude = buildHeaderStatements(c, hdrVar);
            Expression restExpr = buildRestTemplateCall(c, hdrVar, false);
            if (!insertPreludeAndReplaceExpression(getCall, prelude)) {
                continue;
            }
            getCall.replace(restExpr);
            skip.add(getCall);
            any = true;
            result.appliedChanges.add("Rewrote Play WS blocking chain to RestTemplate");
        }
        return any;
    }

    private boolean rewriteWsThenApplyChains(CompilationUnit cu, PlayToSpringTransformer.TransformResult result,
                                             Set<MethodCallExpr> skip) {
        boolean any = false;
        for (MethodCallExpr tail : new ArrayList<>(cu.findAll(MethodCallExpr.class))) {
            if (skip.contains(tail)) {
                continue;
            }
            MethodCallExpr toCf;
            MethodCallExpr outerGet = null;
            if ("toCompletableFuture".equals(tail.getNameAsString()) && tail.getArguments().isEmpty()) {
                toCf = tail;
            } else if (isBareGet(tail)) {
                Optional<MethodCallExpr> inner = tail.getScope().filter(MethodCallExpr.class::isInstance).map(MethodCallExpr.class::cast);
                if (!inner.isPresent() || !"toCompletableFuture".equals(inner.get().getNameAsString())) {
                    continue;
                }
                toCf = inner.get();
                outerGet = tail;
            } else {
                continue;
            }
            if (skip.contains(toCf) || (outerGet != null && skip.contains(outerGet))) {
                continue;
            }
            MethodCallExpr thenApply = toCf.getScope().filter(MethodCallExpr.class::isInstance).map(MethodCallExpr.class::cast).orElse(null);
            if (thenApply == null || !"thenApply".equals(thenApply.getNameAsString()) || thenApply.getArguments().size() != 1) {
                continue;
            }
            if (!isWsResponseAsJsonRef(thenApply.getArgument(0))) {
                continue;
            }
            MethodCallExpr terminal = thenApply.getScope().filter(MethodCallExpr.class::isInstance).map(MethodCallExpr.class::cast).orElse(null);
            if (terminal == null) {
                continue;
            }
            Optional<ParsedWsChain> chain = parseWsRequestChain(terminal);
            if (!chain.isPresent()) {
                continue;
            }
            ParsedWsChain c = chain.get();
            String hdrVar = "playWsHeaders" + syntheticId.incrementAndGet();
            List<Statement> prelude = buildHeaderStatements(c, hdrVar);
            Expression innerRead = buildReadJsonBodyExpr(c, hdrVar);
            Expression replacement;
            if (outerGet != null) {
                replacement = innerRead;
            } else {
                String innerSrc = innerRead.toString();
                Optional<Expression> parsed = parser.parseExpression(
                        "java.util.concurrent.CompletableFuture.supplyAsync(() -> " + innerSrc + ")").getResult();
                if (!parsed.isPresent()) {
                    continue;
                }
                replacement = parsed.get();
            }
            MethodCallExpr toReplace = outerGet != null ? outerGet : toCf;
            if (!insertPreludeAndReplaceExpression(toReplace, prelude)) {
                continue;
            }
            toReplace.replace(replacement);
            skip.add(toReplace);
            skip.add(toCf);
            if (outerGet != null) {
                skip.add(outerGet);
            }
            any = true;
            result.appliedChanges.add("Rewrote Play WS + thenApply(WSResponse::asJson) to RestTemplate/ObjectMapper");
        }
        return any;
    }

    private Expression buildReadJsonBodyExpr(ParsedWsChain c, String hdrVar) {
        Expression rest = buildRestTemplateCall(c, hdrVar, true);
        Optional<Expression> e = parser.parseExpression("objectMapper.readTree(" + rest.toString() + ".getBody())").getResult();
        return e.orElse(rest);
    }

    private boolean isBareGet(MethodCallExpr m) {
        return "get".equals(m.getNameAsString()) && m.getArguments().isEmpty();
    }

    private boolean isWsResponseAsJsonRef(Expression e) {
        if (!(e instanceof MethodReferenceExpr)) {
            return false;
        }
        MethodReferenceExpr r = (MethodReferenceExpr) e;
        Expression scope = r.getScope();
        if (scope == null) {
            return false;
        }
        String ss = scope.toString();
        return "asJson".equals(r.getIdentifier())
                && ("WSResponse".equals(ss) || ss.endsWith(".WSResponse"));
    }

    private Optional<ParsedWsChain> parseWsRequestChain(MethodCallExpr terminal) {
        boolean post;
        String bodyExpr;
        if ("post".equals(terminal.getNameAsString()) && terminal.getArguments().size() == 1) {
            post = true;
            bodyExpr = terminal.getArgument(0).toString();
        } else if ("get".equals(terminal.getNameAsString()) && terminal.getArguments().isEmpty()) {
            post = false;
            bodyExpr = null;
        } else {
            return Optional.empty();
        }
        Expression cursor = terminal.getScope().orElse(null);
        List<String[]> headers = new ArrayList<>();
        String setHeadersMapExpr = null;
        while (cursor instanceof MethodCallExpr) {
            MethodCallExpr mc = (MethodCallExpr) cursor;
            if ("setHeader".equals(mc.getNameAsString()) && mc.getArguments().size() == 2) {
                headers.add(new String[]{mc.getArgument(0).toString(), mc.getArgument(1).toString()});
                cursor = mc.getScope().orElse(null);
            } else if ("setHeaders".equals(mc.getNameAsString()) && mc.getArguments().size() == 1) {
                setHeadersMapExpr = mc.getArgument(0).toString();
                cursor = mc.getScope().orElse(null);
            } else {
                break;
            }
        }
        if (!(cursor instanceof MethodCallExpr)) {
            return Optional.empty();
        }
        MethodCallExpr urlCall = (MethodCallExpr) cursor;
        if (!"url".equals(urlCall.getNameAsString()) || urlCall.getArguments().size() != 1) {
            return Optional.empty();
        }
        Expression scope = urlCall.getScope().orElse(null);
        if (!(scope instanceof NameExpr)) {
            return Optional.empty();
        }
        String client = ((NameExpr) scope).getNameAsString();
        String urlExpr = urlCall.getArgument(0).toString();
        return Optional.of(new ParsedWsChain(client, urlExpr, headers, setHeadersMapExpr, post, bodyExpr));
    }

    private List<Statement> buildHeaderStatements(ParsedWsChain c, String hdrVar) {
        List<Statement> stmts = new ArrayList<>();
        parser.parseStatement("HttpHeaders " + hdrVar + " = new HttpHeaders();").getResult().ifPresent(stmts::add);
        for (String[] kv : c.headers) {
            parser.parseStatement(hdrVar + ".set(" + kv[0] + ", " + kv[1] + ");").getResult().ifPresent(stmts::add);
        }
        if (c.setHeadersMapExpr != null) {
            String block = "for (java.util.Map.Entry<String, java.util.List<String>> __e : " + c.setHeadersMapExpr + ".entrySet()) { "
                    + "for (String __v : __e.getValue()) { " + hdrVar + ".add(__e.getKey(), __v); } }";
            parser.parseStatement(block).getResult().ifPresent(stmts::add);
        }
        return stmts;
    }

    private Expression buildRestTemplateCall(ParsedWsChain c, String hdrVar, boolean stringBody) {
        String typeArg = "String.class";
        String client = c.clientFieldName;
        if (c.post) {
            String entity = "new HttpEntity<>(" + c.bodyExpr + ", " + hdrVar + ")";
            return parser.parseExpression(client + ".postForEntity(" + c.urlExpr + ", " + entity + ", " + typeArg + ")")
                    .getResult().orElseThrow(IllegalStateException::new);
        }
        return parser.parseExpression(
                client + ".exchange(" + c.urlExpr + ", HttpMethod.GET, new HttpEntity<>(" + hdrVar + "), " + typeArg + ")")
                .getResult().orElseThrow(IllegalStateException::new);
    }

    private boolean insertPreludeAndReplaceExpression(Expression targetExpr, List<Statement> prelude) {
        if (prelude.isEmpty()) {
            return true;
        }
        Optional<ReturnStmt> ret = targetExpr.findAncestor(ReturnStmt.class);
        if (ret.isPresent()) {
            Optional<BlockStmt> blockOpt = ret.get().findAncestor(BlockStmt.class);
            if (!blockOpt.isPresent()) {
                return false;
            }
            BlockStmt block = blockOpt.get();
            int idx = block.getStatements().indexOf(ret.get());
            if (idx < 0) {
                return false;
            }
            for (int i = prelude.size() - 1; i >= 0; i--) {
                block.addStatement(idx, prelude.get(i));
            }
            return true;
        }
        Optional<ExpressionStmt> est = targetExpr.findAncestor(ExpressionStmt.class);
        if (!est.isPresent()) {
            return false;
        }
        ExpressionStmt es = est.get();
        Optional<BlockStmt> blockOpt = es.findAncestor(BlockStmt.class);
        if (!blockOpt.isPresent()) {
            return false;
        }
        BlockStmt block = blockOpt.get();
        int idx = block.getStatements().indexOf(es);
        if (idx < 0) {
            return false;
        }
        for (int i = prelude.size() - 1; i >= 0; i--) {
            block.addStatement(idx, prelude.get(i));
        }
        return true;
    }

    private boolean replaceWsResponseMethodCalls(CompilationUnit cu, Set<String> wsResponseNames,
                                                 PlayToSpringTransformer.TransformResult result) {
        if (wsResponseNames.isEmpty()) {
            return false;
        }
        boolean changed = false;
        for (MethodCallExpr m : new ArrayList<>(cu.findAll(MethodCallExpr.class))) {
            Optional<NameExpr> recv = m.getScope().filter(NameExpr.class::isInstance).map(NameExpr.class::cast);
            if (!recv.isPresent() || !wsResponseNames.contains(recv.get().getNameAsString())) {
                continue;
            }
            String mn = m.getNameAsString();
            if ("getStatus".equals(mn) && m.getArguments().isEmpty()) {
                parser.parseExpression(m.getScope().get() + ".getStatusCode().value()").getResult().ifPresent(m::replace);
                changed = true;
            } else if ("getStatusText".equals(mn) && m.getArguments().isEmpty()) {
                parser.parseExpression(m.getScope().get() + ".getStatusCode().getReasonPhrase()").getResult().ifPresent(m::replace);
                changed = true;
            }
        }
        if (changed) {
            result.appliedChanges.add("Mapped WSResponse.getStatus/getStatusText to ResponseEntity status API");
        }
        return changed;
    }

    private Set<String> collectNamesTypedWsResponse(CompilationUnit cu) {
        Set<String> names = new LinkedHashSet<>();
        for (FieldDeclaration fd : cu.findAll(FieldDeclaration.class)) {
            for (VariableDeclarator v : fd.getVariables()) {
                if (isWsResponseType(v.getType())) {
                    names.add(v.getNameAsString());
                }
            }
        }
        for (Parameter p : cu.findAll(Parameter.class)) {
            if (isWsResponseType(p.getType())) {
                names.add(p.getNameAsString());
            }
        }
        for (VariableDeclarator v : cu.findAll(VariableDeclarator.class)) {
            if (v.getParentNode().isPresent()
                    && v.getParentNode().get() instanceof com.github.javaparser.ast.expr.VariableDeclarationExpr) {
                if (isWsResponseType(v.getType())) {
                    names.add(v.getNameAsString());
                }
            }
        }
        return names;
    }

    private boolean isWsResponseType(Type t) {
        String s = t.asString().replace(" ", "");
        return "WSResponse".equals(s) || "play.libs.ws.WSResponse".equals(s);
    }

    private boolean rewriteApplicationLifecycle(ClassOrInterfaceDeclaration clazz, CompilationUnit cu,
                                                PlayToSpringTransformer.TransformResult result) {
        Set<String> lifecycleParamNames = new LinkedHashSet<>();
        for (ConstructorDeclaration cd : clazz.getConstructors()) {
            for (Parameter p : cd.getParameters()) {
                if (isApplicationLifecycleType(p.getType())) {
                    lifecycleParamNames.add(p.getNameAsString());
                }
            }
        }
        if (lifecycleParamNames.isEmpty()) {
            return false;
        }
        boolean changed = false;
        int hookIndex = 0;
        for (MethodCallExpr m : new ArrayList<>(cu.findAll(MethodCallExpr.class))) {
            if (!"addStopHook".equals(m.getNameAsString()) || m.getArguments().size() != 1) {
                continue;
            }
            if (!m.getScope().filter(NameExpr.class::isInstance).map(NameExpr.class::cast)
                    .map(NameExpr::getNameAsString).filter(lifecycleParamNames::contains).isPresent()) {
                continue;
            }
            Expression arg = m.getArgument(0);
            Optional<BlockStmt> lambdaBody = extractLambdaBlock(arg);
            if (!lambdaBody.isPresent()) {
                continue;
            }
            BlockStmt body = stripCompletableFutureReturn(lambdaBody.get());
            String methodName = "playApplicationShutdown" + (hookIndex++);
            MethodDeclaration md = new MethodDeclaration(
                    new NodeList<>(Modifier.publicModifier()),
                    new NodeList<>(new MarkerAnnotationExpr("PreDestroy")),
                    new NodeList<>(),
                    new ClassOrInterfaceType(null, "void"),
                    new SimpleName(methodName),
                    new NodeList<>(),
                    new NodeList<>(),
                    body);
            clazz.addMember(md);
            m.findAncestor(ExpressionStmt.class).ifPresent(ExpressionStmt::remove);
            changed = true;
        }
        if (changed) {
            for (ConstructorDeclaration cd : clazz.getConstructors()) {
                cd.getParameters().removeIf(p -> isApplicationLifecycleType(p.getType()));
            }
            result.appliedChanges.add("Replaced ApplicationLifecycle.addStopHook with @PreDestroy method(s)");
        }
        return changed;
    }

    private Optional<BlockStmt> extractLambdaBlock(Expression arg) {
        if (arg instanceof com.github.javaparser.ast.expr.LambdaExpr) {
            com.github.javaparser.ast.expr.LambdaExpr lam = (com.github.javaparser.ast.expr.LambdaExpr) arg;
            if (lam.getBody() instanceof BlockStmt) {
                return Optional.of((BlockStmt) lam.getBody());
            }
        }
        return Optional.empty();
    }

    private BlockStmt stripCompletableFutureReturn(BlockStmt block) {
        BlockStmt clone = block.clone();
        if (!clone.getStatements().isEmpty()) {
            Statement last = clone.getStatement(clone.getStatements().size() - 1);
            if (last instanceof com.github.javaparser.ast.stmt.ReturnStmt) {
                com.github.javaparser.ast.stmt.ReturnStmt ret = (com.github.javaparser.ast.stmt.ReturnStmt) last;
                if (ret.getExpression().isPresent()) {
                    String ex = ret.getExpression().get().toString();
                    if (ex.contains("CompletableFuture.completedFuture")) {
                        clone.getStatements().remove(clone.getStatements().size() - 1);
                    }
                }
            }
        }
        return clone;
    }

    private boolean isApplicationLifecycleType(Type t) {
        String s = t.asString().replace(" ", "");
        return "ApplicationLifecycle".equals(s) || "play.inject.ApplicationLifecycle".equals(s);
    }

    private boolean replaceWsTypesAndFields(CompilationUnit cu, PlayToSpringTransformer.TransformResult result) {
        boolean changed = false;
        for (FieldDeclaration fd : cu.findAll(FieldDeclaration.class)) {
            for (VariableDeclarator v : fd.getVariables()) {
                if (isWsClientType(v.getType())) {
                    v.setType(new ClassOrInterfaceType(null, "RestTemplate"));
                    changed = true;
                }
                if (isWsResponseType(v.getType())) {
                    ClassOrInterfaceType re = new ClassOrInterfaceType(null, "ResponseEntity");
                    re.setTypeArguments(new NodeList<>(new ClassOrInterfaceType(null, "String")));
                    v.setType(re);
                    changed = true;
                }
            }
        }
        for (Parameter p : cu.findAll(Parameter.class)) {
            if (isWsClientType(p.getType())) {
                p.setType(new ClassOrInterfaceType(null, "RestTemplate"));
                changed = true;
            }
            if (isWsResponseType(p.getType())) {
                ClassOrInterfaceType re = new ClassOrInterfaceType(null, "ResponseEntity");
                re.setTypeArguments(new NodeList<>(new ClassOrInterfaceType(null, "String")));
                p.setType(re);
                changed = true;
            }
        }
        for (VariableDeclarator v : cu.findAll(VariableDeclarator.class)) {
            if (v.getParentNode().isPresent()
                    && v.getParentNode().get() instanceof com.github.javaparser.ast.expr.VariableDeclarationExpr) {
                if (isWsResponseType(v.getType())) {
                    ClassOrInterfaceType re = new ClassOrInterfaceType(null, "ResponseEntity");
                    re.setTypeArguments(new NodeList<>(new ClassOrInterfaceType(null, "String")));
                    v.setType(re);
                    changed = true;
                }
            }
        }
        for (MethodDeclaration md : cu.findAll(MethodDeclaration.class)) {
            if (isWsResponseType(md.getType())) {
                ClassOrInterfaceType re = new ClassOrInterfaceType(null, "ResponseEntity");
                re.setTypeArguments(new NodeList<>(new ClassOrInterfaceType(null, "String")));
                md.setType(re);
                changed = true;
            }
        }
        if (changed) {
            result.appliedChanges.add("Replaced WSClient/WSResponse types with RestTemplate/ResponseEntity<String>");
        }
        return changed;
    }

    private boolean isWsClientType(Type t) {
        String s = t.asString().replace(" ", "");
        return "WSClient".equals(s) || "play.libs.ws.WSClient".equals(s);
    }

    private static final class ParsedWsChain {
        final String clientFieldName;
        final String urlExpr;
        final List<String[]> headers;
        final String setHeadersMapExpr;
        final boolean post;
        final String bodyExpr;

        ParsedWsChain(String clientFieldName, String urlExpr, List<String[]> headers, String setHeadersMapExpr,
                      boolean post, String bodyExpr) {
            this.clientFieldName = clientFieldName;
            this.urlExpr = urlExpr;
            this.headers = headers != null ? headers : Collections.emptyList();
            this.setHeadersMapExpr = setHeadersMapExpr;
            this.post = post;
            this.bodyExpr = bodyExpr;
        }
    }
}
