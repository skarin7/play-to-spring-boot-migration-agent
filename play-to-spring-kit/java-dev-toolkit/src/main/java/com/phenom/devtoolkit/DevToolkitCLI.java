package com.phenom.devtoolkit;

import picocli.CommandLine;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import picocli.CommandLine.Parameters;
import picocli.CommandLine.HelpCommand;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.Callable;
import java.util.stream.Stream;

/**
 * Main CLI entry point for the Java Development Toolkit.
 * Provides unified access to all toolkit functionality through subcommands.
 */
@Command(
    name = "dev-toolkit",
    description = "Java Development Toolkit - Utilities for code refactoring and test generation",
    version = "1.0.0",
    subcommands = {
        HelpCommand.class,
        DevToolkitCLI.SplitCommand.class,
        DevToolkitCLI.VerifyCommand.class,
        DevToolkitCLI.IntrospectCommand.class,
        DevToolkitCLI.TransformCommand.class,
        DevToolkitCLI.MigrateAppCommand.class,
        DevToolkitCLI.GeneratePromptsCommand.class,
        DevToolkitCLI.UndoChangesCommand.class
    }
)
public class DevToolkitCLI implements Callable<Integer> {

    @Option(names = {"-V", "--version"}, versionHelp = true, description = "Display version info")
    boolean versionInfoRequested;

    @Option(names = {"-h", "--help"}, usageHelp = true, description = "Show this help message and exit")
    boolean usageHelpRequested;

    @Override
    public Integer call() throws Exception {
        // If no subcommand is specified, show help
        CommandLine.usage(this, System.out);
        return 0;
    }

    /**
     * Split Command - Split large Java classes into smaller service classes
     */
    @Command(
        name = "split",
        description = "Split a large Java class into smaller service classes with delegation"
    )
    static class SplitCommand implements Callable<Integer> {
        
        @Parameters(index = "0", description = "Input Java file to split")
        private String inputFile;
        
        @Parameters(index = "1", description = "JSON mapping file (class names to method lists)")
        private String mappingFile;
        
        @Parameters(index = "2", arity = "0..1", description = "Output directory for generated classes (optional)")
        private String outputDirectory;
        
        @Option(names = {"--annotation-style"}, 
                description = "Annotation style: ${COMPLETION-CANDIDATES} (default: auto-detect)",
                defaultValue = "auto")
        private String annotationStyle;

        @Option(names = {"--injection-style"}, 
                description = "Dependency injection style: field, constructor (default: field)",
                defaultValue = "field")
        private String injectionStyle;

        @Override
        public Integer call() throws Exception {
            try {
                System.out.println("🔄 Starting class splitting...");
                
                // Resolve paths
                String resolvedInputFile = Paths.get(inputFile).toAbsolutePath().normalize().toString();
                String resolvedMappingFile = Paths.get(mappingFile).toAbsolutePath().normalize().toString();
                String resolvedOutputDir = (outputDirectory == null || outputDirectory.trim().isEmpty()) ? null : Paths.get(outputDirectory).toAbsolutePath().normalize().toString();
                
                // Create splitter and set annotation style and injection style
                JavaClassSplitter splitter = new JavaClassSplitter();
                JavaClassSplitter.AnnotationStyle style = detectAnnotationStyle(annotationStyle);
                splitter.annotationStyle = style; // Ensure the splitter uses the detected style
                System.out.println("Using annotation style: " + style);

                JavaClassSplitter.InjectionStyle injStyle = detectInjectionStyle(injectionStyle);
                splitter.injectionStyle = injStyle;
                System.out.println("Using injection style: " + injStyle);
                
                // Load mapping and split
                Map<String, List<String>> classMethodMapping = splitter.loadMethodMapping(resolvedMappingFile);
                splitter.splitClassWithDelegation(resolvedInputFile, resolvedOutputDir, classMethodMapping);
                
                System.out.println("✅ Class splitting completed successfully!");
                return 0;
                
            } catch (Exception e) {
                System.err.println("❌ Error during class splitting: " + e.getMessage());
                e.printStackTrace();
                return 1;
            }
        }
        
        private JavaClassSplitter.AnnotationStyle detectAnnotationStyle(String style) {
            if ("play".equalsIgnoreCase(style)) return JavaClassSplitter.AnnotationStyle.PLAY;
            if ("java".equalsIgnoreCase(style)) return JavaClassSplitter.AnnotationStyle.JAVA;
            
            // Auto-detect
            if (Files.exists(Paths.get("build.sbt"))) return JavaClassSplitter.AnnotationStyle.PLAY;
            if (Files.exists(Paths.get("pom.xml"))) return JavaClassSplitter.AnnotationStyle.JAVA;
            
            return JavaClassSplitter.AnnotationStyle.JAVA; // Default
        }

        private JavaClassSplitter.InjectionStyle detectInjectionStyle(String style) {
            if ("constructor".equalsIgnoreCase(style)) return JavaClassSplitter.InjectionStyle.CONSTRUCTOR;
            if ("field".equalsIgnoreCase(style)) return JavaClassSplitter.InjectionStyle.FIELD;
            
            return JavaClassSplitter.InjectionStyle.FIELD; // Default
        }
    }

    /**
     * Verify Command - Verify that split results contain all expected methods
     */
    @Command(
        name = "verify",
        description = "Verify that all methods in mapping exist in generated split classes"
    )
    static class VerifyCommand implements Callable<Integer> {
        
        @Parameters(index = "0", description = "Output directory containing split classes")
        private String outputDirectory;
        
        @Parameters(index = "1", description = "JSON mapping file to verify against")
        private String mappingFile;

        @Override
        public Integer call() throws Exception {
            try {
                System.out.println("🔍 Starting verification...");
                
                String resolvedOutputDir = Paths.get(outputDirectory).toAbsolutePath().normalize().toString();
                String resolvedMappingFile = Paths.get(mappingFile).toAbsolutePath().normalize().toString();
                
                JavaClassSplitter splitter = new JavaClassSplitter();
                Map<String, List<String>> classMethodMapping = splitter.loadMethodMapping(resolvedMappingFile);
                splitter.verifySplitResults(resolvedOutputDir, classMethodMapping);
                
                System.out.println("✅ Verification completed!");
                return 0;
                
            } catch (Exception e) {
                System.err.println("❌ Error during verification: " + e.getMessage());
                e.printStackTrace();
                return 1;
            }
        }
    }

    /**
     * Introspect Command - Extract method metadata from Java classes
     */
    @Command(
        name = "introspect",
        description = "Extract metadata about all methods in a Java class"
    )
    static class IntrospectCommand implements Callable<Integer> {
        
        @Parameters(index = "0", description = "Java file to introspect")
        private String inputFile;

        @Override
        public Integer call() throws Exception {
            try {
                System.out.println("🔍 Starting method introspection...");
                
                // Delegate to MethodIntrospector
                String[] args = {inputFile};
                MethodIntrospector.main(args);
                
                System.out.println("✅ Method introspection completed!");
                return 0;
                
            } catch (Exception e) {
                System.err.println("❌ Error during introspection: " + e.getMessage());
                e.printStackTrace();
                return 1;
            }
        }
    }


    /**
     * Transform Command - Transform a single Play Java file into Spring-friendly version.
     * Paths are resolved relative to cwd when not absolute.
     */
    @Command(
        name = "transform",
        description = "Transform a Play Java file into a Spring-friendly version (by layer)"
    )
    static class TransformCommand implements Callable<Integer> {

        @Option(names = {"--input"}, required = true, description = "Input Java file (relative to cwd or absolute)")
        private String inputFile;

        @Option(names = {"--output"}, required = true, description = "Output Java file (relative to cwd or absolute)")
        private String outputFile;

        @Option(names = {"--layer"}, description = "Layer: controller|service|manager|model|repository|other (default: auto-detect from path)")
        private String layer;

        @Option(names = {"--report"}, description = "Optional JSON report path")
        private String reportPath;

        @Override
        public Integer call() throws Exception {
            try {
                Path inputPath = Paths.get(inputFile).toAbsolutePath().normalize();
                Path outputPath = Paths.get(outputFile).toAbsolutePath().normalize();
                LayerDetector.Layer resolvedLayer = layer != null && !layer.isEmpty()
                        ? LayerDetector.fromString(layer)
                        : LayerDetector.classify(inputPath);
                PlayToSpringTransformer transformer = new PlayToSpringTransformer();
                PlayToSpringTransformer.TransformResult result = transformer.transform(inputPath, outputPath, resolvedLayer);

                if (reportPath != null && !reportPath.trim().isEmpty()) {
                    transformer.writeReport(result, Paths.get(reportPath).toAbsolutePath().normalize());
                }

                if (!result.errors.isEmpty()) {
                    System.err.println("❌ Transform completed with errors: " + result.errors);
                    return 1;
                }

                System.out.println("✅ Transform completed successfully");
                return 0;
            } catch (Exception e) {
                System.err.println("❌ Error during transform: " + e.getMessage());
                e.printStackTrace();
                return 1;
            }
        }
    }

    /**
     * Migrate-App Command - Walk Play {@code app/} and JUnit sources under {@code src/test/java} or legacy {@code test/},
     * transforming Java files into the Spring repo ({@code src/main/java} and {@code src/test/java}).
     */
    @Command(
        name = "migrate-app",
        description = "Migrate Play app/ and tests (src/test/java or test/) to Spring src/main/java and src/test/java"
    )
    static class MigrateAppCommand implements Callable<Integer> {

        @Option(names = {"--source"}, description = "Play repo root (default: .)")
        private String source = ".";

        @Option(names = {"--target"}, description = "Spring repo root (default: ../spring-<basename(source)> or from workspace.yaml)")
        private String target;

        @Option(names = {"--layer"}, description = "Filter to one layer: model, repository, manager, service, controller, other. When not set, all layers are processed.")
        private String layerFilter;

        @Option(names = {"--batch-size"}, description = "Max files to process in this invocation (default: all). Skips files already migrated to target.")
        private int batchSize = -1;

        @Option(names = {"--report"}, description = "Optional JSON report path")
        private String reportPath;

        @Option(names = {"--dry-run"}, description = "Only log what would be done, do not write files")
        private boolean dryRun;

        @Override
        public Integer call() throws Exception {
            try {
                Path cwd = Paths.get("").toAbsolutePath().normalize();
                Path sourcePath = Paths.get(source).toAbsolutePath().normalize();
                Path appDir = sourcePath.resolve("app");
                if (!Files.isDirectory(appDir)) {
                    System.err.println("❌ Source app/ not found: " + appDir);
                    return 1;
                }
                Path targetPath = resolveTarget(cwd, sourcePath);
                Path javaRoot = targetPath.resolve("src/main/java");
                Path springTestRoot = targetPath.resolve("src/test/java");
                if (!dryRun) {
                    Files.createDirectories(javaRoot);
                }

                LayerDetector.Layer requestedLayer = null;
                if (layerFilter != null && !layerFilter.trim().isEmpty()) {
                    requestedLayer = LayerDetector.fromString(layerFilter);
                }

                PlayToSpringTransformer transformer = new PlayToSpringTransformer();
                List<PlayToSpringTransformer.TransformResult> results = new ArrayList<>();
                final LayerDetector.Layer filterLayer = requestedLayer;
                int[] skippedCount = {0};
                int[] remainingCount = {0};

                processPlayJavaTree(appDir, javaRoot, transformer, results, filterLayer, skippedCount, remainingCount,
                        batchSize, dryRun, PlayToSpringTransformer.PlayMigrationSource.APPLICATION, "app");

                Optional<Path> playTestRoot = PlayToSpringTransformer.resolvePlayTestJavaSourceRoot(sourcePath);
                if (playTestRoot.isPresent()) {
                    if (!dryRun) {
                        Files.createDirectories(springTestRoot);
                    }
                    processPlayJavaTree(playTestRoot.get(), springTestRoot, transformer, results, filterLayer,
                            skippedCount, remainingCount, batchSize, dryRun,
                            PlayToSpringTransformer.PlayMigrationSource.TEST, "test");
                }

                if (reportPath != null && !reportPath.trim().isEmpty() && !results.isEmpty()) {
                    Path report = Paths.get(reportPath).toAbsolutePath().normalize();
                    Files.createDirectories(report.getParent());
                    new ObjectMapper().writerWithDefaultPrettyPrinter()
                            .writeValue(report.toFile(), results);
                    System.out.println("Wrote report: " + report);
                }

                long failed = results.stream().filter(r -> !r.errors.isEmpty()).count();
                String layerInfo = filterLayer != null ? " (layer=" + filterLayer + ")" : "";
                System.out.println("✅ migrate-app done: " + results.size() + " files, " + failed + " errors, " + remainingCount[0] + " remaining" + layerInfo);
                return failed > 0 ? 1 : 0;
            } catch (Exception e) {
                System.err.println("❌ Error during migrate-app: " + e.getMessage());
                e.printStackTrace();
                return 1;
            }
        }

        private Path resolveTarget(Path cwd, Path sourcePath) {
            if (target != null && !target.trim().isEmpty()) {
                return Paths.get(target).toAbsolutePath().normalize();
            }
            Optional<Path> fromYaml = readSpringRepoFromWorkspace(cwd, sourcePath);
            if (fromYaml.isPresent()) {
                return fromYaml.get();
            }
            String basename = sourcePath.getFileName() != null ? sourcePath.getFileName().toString() : "app";
            return sourcePath.getParent().resolve("spring-" + basename);
        }

        private void processPlayJavaTree(
                Path sourceRoot,
                Path targetRoot,
                PlayToSpringTransformer transformer,
                List<PlayToSpringTransformer.TransformResult> results,
                LayerDetector.Layer filterLayer,
                int[] skippedCount,
                int[] remainingCount,
                int batchSize,
                boolean dryRun,
                PlayToSpringTransformer.PlayMigrationSource migrationSource,
                String treeLabel) throws IOException {

            List<Path> candidates;
            try (Stream<Path> walk = Files.walk(sourceRoot)) {
                candidates = walk.filter(p -> p.toString().endsWith(".java"))
                        .sorted()
                        .collect(java.util.stream.Collectors.toList());
            }

            for (Path p : candidates) {
                Path rel = sourceRoot.relativize(p);
                Path outPath = targetRoot.resolve(rel);
                LayerDetector.Layer detectedLayer = PlayToSpringTransformer.migrationLayer(rel, migrationSource);

                if (filterLayer != null && detectedLayer != filterLayer) {
                    continue;
                }

                if (Files.exists(outPath)) {
                    skippedCount[0]++;
                    continue;
                }

                if (batchSize > 0 && results.size() >= batchSize) {
                    remainingCount[0]++;
                    continue;
                }

                if (dryRun) {
                    System.out.println("Would transform [" + treeLabel + "] " + rel + " -> " + outPath
                            + " (layer=" + detectedLayer + ")");
                    results.add(new PlayToSpringTransformer.TransformResult());
                    continue;
                }

                try {
                    Files.createDirectories(outPath.getParent());
                    PlayToSpringTransformer.TransformResult result = transformer.transform(p, outPath, detectedLayer);
                    results.add(result);
                    if (!result.warnings.isEmpty()) {
                        System.out.println("  [" + treeLabel + "] " + rel + ": " + result.warnings);
                    }
                } catch (IOException e) {
                    PlayToSpringTransformer.TransformResult err = new PlayToSpringTransformer.TransformResult();
                    err.input = p.toString();
                    err.output = outPath.toString();
                    err.errors.add(e.getMessage());
                    results.add(err);
                }
            }
        }

        private Optional<Path> readSpringRepoFromWorkspace(Path cwd, Path sourcePath) {
            for (Path dir : Arrays.asList(cwd, sourcePath, cwd.resolve(".play-to-spring-kit"))) {
                Path yaml = dir.resolve("workspace.yaml");
                if (!Files.isRegularFile(yaml)) continue;
                try {
                    for (String line : Files.readAllLines(yaml)) {
                        line = line.trim();
                        if (line.startsWith("spring_repo:")) {
                            String value = line.substring("spring_repo:".length()).trim();
                            if (!value.isEmpty()) {
                                return Optional.of(Paths.get(value).toAbsolutePath().normalize());
                            }
                        }
                    }
                } catch (IOException ignored) { }
            }
            return Optional.empty();
        }
    }

    /**
     * Generate Prompts Command - Generate JUnit test prompts for LLMs
     */
    @Command(
        name = "generate-prompts",
        description = "Generate structured prompts for JUnit test creation with LLMs"
    )
    static class GeneratePromptsCommand implements Callable<Integer> {
        
        @Option(names = {"--package"}, required = true,
                description = "Path to the package (directory) to search")
        private String packagePath;
        
        @Option(names = {"--class"}, 
                description = "Name of the Java class (without .java) - optional")
        private String className;
        
        @Option(names = {"--output"}, 
                description = "Output file name - optional")
        private String outputFile;

        @Override
        public Integer call() throws Exception {
            try {
                System.out.println("📝 Starting test prompt generation...");
                
                if (className != null) {
                    // Process specific class
                    Optional<Path> javaFile = TestPromptGenerator.findJavaFileByClass(packagePath, className);
                    if (!javaFile.isPresent()) {
                        System.err.println("❌ Could not find " + className + ".java in " + packagePath);
                        return 1;
                    }
                    TestPromptGenerator.processJavaFile(javaFile.get(), outputFile);
                } else {
                    // Process all Java files in package
                    List<Path> javaFiles = TestPromptGenerator.findAllJavaFiles(packagePath);
                    if (javaFiles.isEmpty()) {
                        System.err.println("❌ No Java files found in " + packagePath);
                        return 1;
                    }
                    
                    for (Path javaFile : javaFiles) {
                        TestPromptGenerator.processJavaFile(javaFile, null);
                    }
                }
                
                System.out.println("✅ Test prompt generation completed!");
                return 0;
                
            } catch (Exception e) {
                System.err.println("❌ Error during prompt generation: " + e.getMessage());
                e.printStackTrace();
                return 1;
            }
        }
    }

    /**
     * Undo Changes Command - Restore the original class and remove generated files
     */
    @Command(
        name = "undoChanges",
        description = "Restore the original class and remove generated files using the .split_undo.json manifest"
    )
    static class UndoChangesCommand implements Callable<Integer> {

        @Parameters(index = "0", description = "Output directory containing split classes (optional)", arity = "0..1")
        private String outputDirectory;

        @Override
        public Integer call() throws Exception {
            try {
                System.out.println("⏪ Starting undo operation...");
                JavaClassSplitter splitter = new JavaClassSplitter();
                // If not provided, fallback to current directory (for compatibility, but method ignores it)
                String dir = (outputDirectory == null || outputDirectory.trim().isEmpty()) ? System.getProperty("user.dir") : outputDirectory;
                splitter.undoChanges(dir);
                System.out.println("✅ Undo operation completed!");
                return 0;
            } catch (Exception e) {
                System.err.println("❌ Error during undo operation: " + e.getMessage());
                e.printStackTrace();
                return 1;
            }
        }
    }

    /**
     * Execute the CLI without calling System.exit (useful for testing)
     */
    public static int execute(String[] args) {
        return new CommandLine(new DevToolkitCLI()).execute(args);
    }

    /**
     * Main entry point
     */
    public static void main(String[] args) {
        int exitCode = execute(args);
        System.exit(exitCode);
    }
} 