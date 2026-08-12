package com.phenom.devtoolkit;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.type.ClassOrInterfaceType;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * Pre-flight scan of a Play Java project's API surface, run before {@code migrate-app}.
 *
 * <p>Classifies every Play/DI/actor import touchpoint the toolkit finds into:
 * <ul>
 *   <li><b>KNOWN</b> — a {@code PlayToSpringTransformer}/{@code SpringCompileFixups} rewrite rule
 *       already covers this construct (controllers, Result factories, DI annotations, Json, WS,
 *       lifecycle hooks, logging, Http request/status).</li>
 *   <li><b>PARADIGM</b> — a structural mismatch with no Spring equivalent to rewrite to
 *       (Akka/Pekko actors today; extensible to other paradigm-shift frameworks).</li>
 *   <li><b>UNKNOWN</b> — a Play API the toolkit does not yet transform (e.g. {@code play.data.Form}).</li>
 * </ul>
 *
 * <p>This exists so migration coverage is knowable up front instead of discovered by a broken
 * build: unknown/paradigm gaps are reported before transforming, rather than silently left as
 * un-migrated {@code play.*} references that later feed an unwinnable compile-fix loop.
 */
public class PlaySurfaceInventory {

    public static final String KNOWN = "KNOWN";
    public static final String PARADIGM = "PARADIGM";
    public static final String UNKNOWN = "UNKNOWN";

    /** Prefix on a {@code TransformResult} warning for a file {@code migrate-app} skipped
     * because it contains a non-KNOWN touchpoint. Greppable so downstream (Python) tooling
     * can distinguish a real skip from an ordinary transform warning. */
    public static final String GAP_SKIP_PREFIX = "SKIPPED ";

    /** One classified Play API touchpoint. */
    public static class Touchpoint {
        public String construct;
        public String location;
        public String classification;
        public String detail;

        public Touchpoint() {
        }

        public Touchpoint(String construct, String location, String classification, String detail) {
            this.construct = construct;
            this.location = location;
            this.classification = classification;
            this.detail = detail;
        }
    }

    /** Aggregate scan result for a project tree. */
    public static class Report {
        public int filesScanned;
        public List<Touchpoint> touchpoints = new ArrayList<>();
        public int knownCount;
        public int unknownCount;
        public int paradigmCount;
        public double coveragePercent;
        public boolean hasRoutesFile;
        public boolean hasApplicationConf;
    }

    /**
     * Imports the toolkit already has a deterministic rewrite rule for
     * ({@code PlayToSpringTransformer} / {@code SpringCompileFixups} / {@code PlayWsSpringRewriter}).
     */
    private static final List<String> KNOWN_IMPORT_PREFIXES = Arrays.asList(
            "play.mvc.Controller",
            "play.mvc.Result",
            "play.mvc.Http",
            "play.libs.Json",
            "play.libs.ws.",
            "play.inject.ApplicationLifecycle",
            "play.Logger",
            "play.api.Logger",
            "javax.inject.Inject",
            "javax.inject.Singleton",
            "javax.inject.Provider",
            "com.google.inject.Inject",
            "com.google.inject.Singleton",
            "com.google.inject.Provider"
    );

    /** Imports for frameworks with no Spring structural equivalent — a design decision, not a bug. */
    private static final List<String> PARADIGM_IMPORT_PREFIXES = Arrays.asList(
            "akka.",
            "org.apache.pekko."
    );

    /**
     * Simple class names matching {@link #KNOWN_IMPORT_PREFIXES}, used to resolve a wildcard
     * import (e.g. {@code import play.mvc.*;}) whose import name is just the package, not a
     * class, so it never matches a full-class prefix.
     */
    private static final Set<String> KNOWN_SIMPLE_NAMES = new LinkedHashSet<>(Arrays.asList(
            "Controller", "Result", "Http", "Json", "ApplicationLifecycle", "Logger",
            "Inject", "Singleton", "Provider", "WSClient", "WSResponse"
    ));

    /** Simple class names matching {@link #PARADIGM_IMPORT_PREFIXES}, same wildcard-resolution need. */
    private static final Set<String> PARADIGM_SIMPLE_NAMES = new LinkedHashSet<>(Arrays.asList(
            "ActorSystem", "Scheduler", "UntypedActor", "AbstractActor", "AbstractActorWithTimers",
            "AbstractLoggingActor", "ActorRef", "Props", "ActorRefFactory"
    ));

    /**
     * Scan a single Java file and return one {@link Touchpoint} per {@code play.*} / DI / actor
     * import found, classified KNOWN, PARADIGM, or UNKNOWN. Non-Play imports are ignored.
     * Parse failures produce no touchpoints (the same file will surface as a transform warning
     * elsewhere; inventory is best-effort, not a second parser).
     */
    public List<Touchpoint> scanFile(Path javaFile) throws IOException {
        List<Touchpoint> touchpoints = new ArrayList<>();
        String source = new String(Files.readAllBytes(javaFile));
        ParseResult<CompilationUnit> parseResult = new JavaParser().parse(source);
        if (!parseResult.isSuccessful() || !parseResult.getResult().isPresent()) {
            return touchpoints;
        }
        CompilationUnit cu = parseResult.getResult().get();
        Set<String> explicitlyImportedSimpleNames = new LinkedHashSet<>();
        for (ImportDeclaration imp : cu.getImports()) {
            if (!imp.isAsterisk()) {
                explicitlyImportedSimpleNames.add(imp.getName().getIdentifier());
            }
        }
        for (ImportDeclaration imp : cu.getImports()) {
            String name = imp.getNameAsString();
            int line = imp.getBegin().map(p -> p.line).orElse(-1);
            String location = javaFile + ":" + line;
            if (imp.isAsterisk()) {
                touchpoints.addAll(scanWildcardImportUsage(cu, name, location, explicitlyImportedSimpleNames));
                continue;
            }
            if (!isRelevantImport(name)) {
                continue;
            }
            String classification = classify(name);
            touchpoints.add(new Touchpoint(name, location, classification, null));
        }
        return touchpoints;
    }

    /**
     * A wildcard import's name is just the package (e.g. {@code "play.mvc"} for
     * {@code import play.mvc.*;}), which never matches a full-class prefix like
     * {@code "play.mvc.Controller"}. Fall back to resolving which curated known/paradigm simple
     * class names are actually referenced in the file body (type usage + static-style call
     * scopes). Best-effort: a simple name from the wildcard's package that isn't in either
     * curated set produces no touchpoint -- the same disclosed limitation as an explicit import
     * of an uncatalogued class, not a new one.
     */
    private List<Touchpoint> scanWildcardImportUsage(
            CompilationUnit cu, String wildcardPackage, String location, Set<String> explicitlyImportedSimpleNames) {
        List<Touchpoint> touchpoints = new ArrayList<>();
        boolean relevant = wildcardPackage.startsWith("play.") || matchesAny(wildcardPackage + ".", PARADIGM_IMPORT_PREFIXES);
        if (!relevant) {
            return touchpoints;
        }
        for (String simpleName : referencedSimpleNames(cu)) {
            if (explicitlyImportedSimpleNames.contains(simpleName)) {
                // Already has its own explicit-import touchpoint with the correct package --
                // attributing it to this wildcard too would double-count it under the wrong FQN.
                continue;
            }
            String classification;
            if (PARADIGM_SIMPLE_NAMES.contains(simpleName)) {
                classification = PARADIGM;
            } else if (KNOWN_SIMPLE_NAMES.contains(simpleName)) {
                classification = KNOWN;
            } else {
                continue;
            }
            touchpoints.add(new Touchpoint(wildcardPackage + "." + simpleName + " (via wildcard)", location, classification, null));
        }
        return touchpoints;
    }

    /** Type usages (extends/return/param types) and method-call-scope names in the file body. */
    private Set<String> referencedSimpleNames(CompilationUnit cu) {
        Set<String> names = new LinkedHashSet<>();
        for (ClassOrInterfaceType t : cu.findAll(ClassOrInterfaceType.class)) {
            names.add(t.getNameAsString());
        }
        for (MethodCallExpr call : cu.findAll(MethodCallExpr.class)) {
            call.getScope().ifPresent(scope -> {
                if (scope instanceof NameExpr) {
                    names.add(((NameExpr) scope).getNameAsString());
                }
            });
        }
        return names;
    }

    private boolean isRelevantImport(String name) {
        return name.startsWith("play.")
                || matchesAny(name, PARADIGM_IMPORT_PREFIXES)
                || matchesAny(name, KNOWN_IMPORT_PREFIXES);
    }

    private String classify(String importName) {
        if (matchesAny(importName, PARADIGM_IMPORT_PREFIXES)) {
            return PARADIGM;
        }
        if (matchesAny(importName, KNOWN_IMPORT_PREFIXES)) {
            return KNOWN;
        }
        return UNKNOWN;
    }

    private boolean matchesAny(String name, List<String> prefixes) {
        for (String prefix : prefixes) {
            if (name.equals(prefix) || name.startsWith(prefix)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Scan a Play project root: walks {@code app/} (falling back to the root itself if {@code app/}
     * doesn't exist, e.g. in tests), classifies every touchpoint, and computes coverage.
     * Also records presence of {@code conf/routes} and {@code conf/application.conf}, since neither
     * is transformed by the toolkit today and both should be visible in the pre-flight picture.
     */
    public Report scanTree(Path projectRoot) throws IOException {
        Report report = new Report();
        Path appDir = projectRoot.resolve("app");
        Path scanRoot = Files.isDirectory(appDir) ? appDir : projectRoot;

        List<Path> javaFiles;
        try (Stream<Path> walk = Files.walk(scanRoot)) {
            javaFiles = walk.filter(p -> p.toString().endsWith(".java"))
                    .sorted()
                    .collect(Collectors.toList());
        }

        Set<Path> distinctFiles = new LinkedHashSet<>(javaFiles);
        report.filesScanned = distinctFiles.size();
        for (Path file : distinctFiles) {
            report.touchpoints.addAll(scanFile(file));
        }

        for (Touchpoint t : report.touchpoints) {
            switch (t.classification) {
                case KNOWN:
                    report.knownCount++;
                    break;
                case PARADIGM:
                    report.paradigmCount++;
                    break;
                default:
                    report.unknownCount++;
            }
        }
        int total = report.knownCount + report.unknownCount + report.paradigmCount;
        report.coveragePercent = total == 0 ? 100.0 : (100.0 * report.knownCount / total);

        report.hasRoutesFile = Files.exists(projectRoot.resolve("conf/routes"));
        report.hasApplicationConf = Files.exists(projectRoot.resolve("conf/application.conf"));
        return report;
    }
}
