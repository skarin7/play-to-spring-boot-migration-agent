package com.phenom.devtoolkit;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import com.github.javaparser.ast.Modifier;
import java.util.stream.Stream;
import java.io.InputStream;
import java.util.LinkedList;
import java.util.HashSet;
import java.util.Set;
import java.util.StringJoiner;

/**
 * Generates JUnit test prompts for Java classes and methods.
 * This tool extracts methods from Java files and creates structured prompts
 * that can be used with LLMs to generate comprehensive JUnit tests.
 */
public class TestPromptGenerator {
    
    private static final String PROMPTS_DIR = "prompts";
    private static final int MAX_BATCH_LINES = 250;
    private static final String TEMPLATE_PATH = "/sample_template.txt";
    private static final String CONTEXT_TEMPLATE_PATH = "/sample_template.txt";
    private static final String BATCH_TEMPLATE_PATH = "/sample_template.txt";
    
    /**
     * Data class to hold method information
     */
    public static class MethodInfo {
        private final String name;
        private final String code;
        
        public MethodInfo(String name, String code) {
            this.name = name;
            this.code = code;
        }
        
        public String getName() { return name; }
        public String getCode() { return code; }
    }
    
    /**
     * Find a Java file by class name in the given directory and subdirectories
     */
    public static Optional<Path> findJavaFileByClass(String directory, String className) {
        try (Stream<Path> paths = Files.walk(Paths.get(directory))) {
            return paths
                .filter(Files::isRegularFile)
                .filter(path -> path.getFileName().toString().equals(className + ".java"))
                .findFirst();
        } catch (IOException e) {
            System.err.println("Error searching for Java file: " + e.getMessage());
            return Optional.empty();
        }
    }
    
    /**
     * Change all private method declarations to protected in the Java file using JavaParser
     */
    public static void changePrivateToProtected(Path filePath) {
        try {
            byte[] bytes = Files.readAllBytes(filePath);
            String content = new String(bytes, "UTF-8");
            
            JavaParser parser = new JavaParser();
            ParseResult<CompilationUnit> parseResult = parser.parse(content);
            
            if (!parseResult.isSuccessful()) {
                System.err.println("Failed to parse Java file for private-to-protected conversion: " + filePath);
                parseResult.getProblems().forEach(problem -> 
                    System.err.println("Parse error: " + problem.getMessage()));
                return;
            }
            
            CompilationUnit cu = parseResult.getResult().get();
            boolean hasChanges = false;
            
            // Find all method declarations and change private to protected
            List<MethodDeclaration> methods = cu.findAll(MethodDeclaration.class);
            for (MethodDeclaration method : methods) {
                if (method.getModifiers().contains(Modifier.privateModifier())) {
                    method.getModifiers().remove(Modifier.privateModifier());
                    method.getModifiers().add(Modifier.protectedModifier());
                    hasChanges = true;
                }
            }
            
            if (hasChanges) {
                // Write the modified content back to file
                String modifiedContent = cu.toString();
                Files.write(filePath, modifiedContent.getBytes("UTF-8"));
                System.out.println("Changed private methods to protected in: " + filePath.getFileName());
            } else {
                System.out.println("No private methods found to convert in: " + filePath.getFileName());
            }
            
        } catch (IOException e) {
            System.err.println("Error modifying file " + filePath + ": " + e.getMessage());
        }
    }
    
    /**
     * Extract all methods with their complete code from a Java file
     */
    public static List<MethodInfo> extractAllMethodsWithCode(Path filePath) {
        List<MethodInfo> methods = new ArrayList<>();
        
        try {
            byte[] bytes = Files.readAllBytes(filePath);
            String content = new String(bytes, "UTF-8");
            JavaParser parser = new JavaParser();
            ParseResult<CompilationUnit> parseResult = parser.parse(content);
            
            if (!parseResult.isSuccessful()) {
                System.err.println("Failed to parse Java file: " + filePath);
                parseResult.getProblems().forEach(problem -> 
                    System.err.println("Parse error: " + problem.getMessage()));
                return methods;
            }
            
            CompilationUnit cu = parseResult.getResult().get();
            
            // Visit all method declarations
            cu.accept(new VoidVisitorAdapter<Void>() {
                @Override
                public void visit(MethodDeclaration method, Void arg) {
                    super.visit(method, arg);
                    
                    // Extract method code including signature and body
                    String methodCode = method.toString();
                    methods.add(new MethodInfo(method.getNameAsString(), methodCode));
                }
            }, null);
            
        } catch (IOException e) {
            System.err.println("Error reading file " + filePath + ": " + e.getMessage());
        }
        
        return methods;
    }
    
    /**
     * Extract class name from Java content
     */
    public static Optional<String> extractClassName(String content) {
        try {
            JavaParser parser = new JavaParser();
            ParseResult<CompilationUnit> parseResult = parser.parse(content);
            
            if (!parseResult.isSuccessful()) {
                return Optional.empty();
            }
            
            CompilationUnit cu = parseResult.getResult().get();
            
            // Find the first public class
            Optional<ClassOrInterfaceDeclaration> classDecl = cu.findFirst(ClassOrInterfaceDeclaration.class,
                cls -> cls.isPublic() && !cls.isInterface());
            
            return classDecl.map(cls -> cls.getNameAsString());
            
        } catch (Exception e) {
            System.err.println("Error extracting class name: " + e.getMessage());
            return Optional.empty();
        }
    }
    
    /**
     * Generate a comprehensive JUnit test prompt for a method
     */
    public static String generateTestPrompt(String methodName, String methodCode, String className) {
        return String.format(
            "Generate a comprehensive JUnit test for the %s method in %s class having code\n" +
            "%s\n" +
            "The test should:\n\n" +
            "1. Include proper mocking of all dependencies using Mockito\n" +
            "2. Test both success and failure scenarios\n" +
            "3. Include edge cases and boundary conditions\n" +
            "4. Have high code coverage\n" +
            "5. Follow best practices for test organization and readability\n" +
            "6. Test any complex business logic thoroughly\n\n" +
            "The test should be written in a way that it can be easily maintained and understood by other developers.",
            methodName, className, methodCode
        );
    }
    
    /**
     * Read the prompt template from resources
     */
    private static String readPromptTemplate() throws IOException {
        try (InputStream in = TestPromptGenerator.class.getResourceAsStream(TEMPLATE_PATH)) {
            if (in == null) throw new IOException("Prompt template not found: " + TEMPLATE_PATH);
            java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
            byte[] data = new byte[4096];
            int nRead;
            while ((nRead = in.read(data, 0, data.length)) != -1) {
                buffer.write(data, 0, nRead);
            }
            buffer.flush();
            return new String(buffer.toByteArray(), "UTF-8");
        }
    }

    /**
     * Extract import statements from a CompilationUnit
     */
    private static String extractImports(CompilationUnit cu) {
        StringJoiner joiner = new StringJoiner("\n");
        cu.getImports().forEach(imp -> joiner.add(imp.toString().trim()));
        return joiner.toString();
    }

    /**
     * Extract constructor code from a CompilationUnit
     */
    private static String extractConstructor(CompilationUnit cu, String className) {
        return cu.findFirst(ClassOrInterfaceDeclaration.class)
            .flatMap(cls -> cls.getConstructors().stream().findFirst())
            .map(constructor -> constructor.toString())
            .orElse("");
    }

    /**
     * Extract dependencies (fields and called methods) for a list of methods, enriched for LLM context
     */
    private static String extractDependencies(CompilationUnit cu, List<MethodDeclaration> methods) {
        StringBuilder sb = new StringBuilder();
        Set<String> dependencyFields = new HashSet<>();
        Set<String> methodCalls = new HashSet<>();
        // 1. Collect all field declarations
        cu.findFirst(ClassOrInterfaceDeclaration.class).ifPresent(cls -> {
            cls.getFields().forEach(field -> {
                String fieldStr = field.toString().replaceAll("\\s+", " ").trim();
                dependencyFields.add(fieldStr);
            });
        });
        // 2. For each method, collect all method calls
        for (MethodDeclaration method : methods) {
            method.findAll(com.github.javaparser.ast.expr.MethodCallExpr.class).forEach(call -> {
                String scope = call.getScope().map(Object::toString).orElse("");
                String methodName = call.getNameAsString();
                int argCount = call.getArguments().size();
                // Try to resolve the type of the scope (if it's a field)
                final String[] typeHint = {""};
                if (!scope.isEmpty()) {
                    // Try to find the field declaration for this scope
                    cu.findFirst(ClassOrInterfaceDeclaration.class).ifPresent(cls -> {
                        cls.getFields().forEach(field -> {
                            field.getVariables().forEach(var -> {
                                if (var.getNameAsString().equals(scope)) {
                                    typeHint[0] = field.getElementType().asString();
                                }
                            });
                        });
                    });
                }
                String callStr;
                if (!scope.isEmpty()) {
                    callStr = scope + "." + methodName + "(" + argCount + " args)";
                    if (!typeHint[0].isEmpty()) {
                        callStr += "  // type: " + typeHint[0];
                    }
                } else {
                    callStr = methodName + "(" + argCount + " args)";
                }
                methodCalls.add(callStr);
            });
        }
        // 3. Format output
        sb.append("---DEPENDENCY FIELDS---\n");
        if (dependencyFields.isEmpty()) {
            sb.append("(none found)\n");
        } else {
            dependencyFields.forEach(f -> sb.append(f).append("\n"));
        }
        sb.append("\n---METHOD CALLS---\n");
        if (methodCalls.isEmpty()) {
            sb.append("(none found)\n");
        } else {
            methodCalls.forEach(m -> sb.append(m).append("\n"));
        }
        // 4. (Optional) Keep old calls: ... for backward compatibility
        sb.append("\n---RAW CALLS---\n");
        for (MethodDeclaration method : methods) {
            method.findAll(com.github.javaparser.ast.expr.MethodCallExpr.class)
                .forEach(call -> sb.append("calls: ").append(call.getNameAsString()).append("\n"));
        }
        return sb.toString().trim();
    }

    /**
     * Extract method summary (signature + Javadoc if present)
     */
    private static String extractMethodSummary(MethodDeclaration method) {
        StringBuilder sb = new StringBuilder();
        method.getJavadocComment().ifPresent(javadoc -> sb.append(javadoc.toString()).append("\n"));
        sb.append(method.getDeclarationAsString(false, false, false));
        return sb.toString();
    }

    /**
     * Batch methods by total lines, max MAX_BATCH_LINES per batch
     */
    private static List<List<MethodDeclaration>> batchMethods(List<MethodDeclaration> allMethods) {
        List<List<MethodDeclaration>> batches = new ArrayList<>();
        List<MethodDeclaration> currentBatch = new ArrayList<>();
        int currentLines = 0;
        for (MethodDeclaration method : allMethods) {
            int lines = method.toString().split("\n").length;
            if (!currentBatch.isEmpty() && (currentLines + lines > MAX_BATCH_LINES)) {
                batches.add(new ArrayList<>(currentBatch));
                currentBatch.clear();
                currentLines = 0;
            }
            currentBatch.add(method);
            currentLines += lines;
        }
        if (!currentBatch.isEmpty()) {
            batches.add(currentBatch);
        }
        return batches;
    }

    /**
     * Read a specific section from a template file
     */
    private static String readSectionFromTemplate(String section) throws IOException {
        try (InputStream in = TestPromptGenerator.class.getResourceAsStream(TEMPLATE_PATH)) {
            if (in == null) throw new IOException("Prompt template not found: " + TEMPLATE_PATH);
            java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
            byte[] data = new byte[4096];
            int nRead;
            while ((nRead = in.read(data, 0, data.length)) != -1) {
                buffer.write(data, 0, nRead);
            }
            buffer.flush();
            String template = new String(buffer.toByteArray(), "UTF-8");
            // Extract section
            String start = "===" + section + "===";
            String end = "===END_" + section + "===";
            int startIdx = template.indexOf(start);
            int endIdx = template.indexOf(end);
            if (startIdx == -1 || endIdx == -1) throw new IOException("Section not found: " + section);
            return template.substring(startIdx + start.length(), endIdx).trim();
        }
    }

    /**
     * Extract class Javadoc from a CompilationUnit
     */
    private static String extractClassJavadoc(CompilationUnit cu) {
        return cu.findFirst(ClassOrInterfaceDeclaration.class)
            .flatMap(cls -> cls.getJavadocComment().map(Object::toString))
            .orElse("");
    }

    /**
     * Generate a context file for the class
     */
    private static void generateContextFile(String className, CompilationUnit cu, List<MethodDeclaration> allMethods) throws IOException {
        String contextTemplate = readSectionFromTemplate("CONTEXT");
        String context = contextTemplate
            .replace("{{current_class}}", className)
            .replace("{{import_statements}}", extractImports(cu))
            .replace("{{current_method_dependencies}}", extractDependencies(cu, allMethods))
            .replace("{{constructor}}", extractConstructor(cu, className))
            .replace("{{class_javadoc}}", extractClassJavadoc(cu));
        Path promptsDir = Paths.get(PROMPTS_DIR);
        if (!Files.exists(promptsDir)) {
            Files.createDirectories(promptsDir);
        }
        Path contextPath = promptsDir.resolve(className + "_junit_context.txt");
        Files.write(contextPath, context.getBytes("UTF-8"), StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
        System.out.println("Generated context file in " + contextPath);
    }

    /**
     * Generate a prompt for a batch of methods using the batch template
     */
    private static String generateBatchPromptFromTemplate(String template, String className, List<MethodDeclaration> methods) {
        String methodNames = methods.stream().map(MethodDeclaration::getNameAsString).reduce((a, b) -> a + ", " + b).orElse("");
        StringBuilder methodsCode = new StringBuilder();
        for (MethodDeclaration method : methods) {
            methodsCode.append("// ").append(extractMethodSummary(method)).append("\n");
            methodsCode.append(method.toString()).append("\n\n");
        }
        String prompt = template
            .replace("{{current_class}}", className)
            .replace("{{methodNames}}", methodNames)
            .replace("{{methods_code}}", methodsCode.toString().trim());
        return prompt;
    }

    /**
     * Process a single Java file and generate context + batch prompts
     */
    public static void processJavaFile(Path javaFile, String outputFileName) {
        try {
            // changePrivateToProtected(javaFile);
            byte[] bytes = Files.readAllBytes(javaFile);
            String content = new String(bytes, "UTF-8");
            JavaParser parser = new JavaParser();
            ParseResult<CompilationUnit> parseResult = parser.parse(content);
            if (!parseResult.isSuccessful()) {
                System.err.println("Failed to parse Java file: " + javaFile);
                parseResult.getProblems().forEach(problem -> System.err.println("Parse error: " + problem.getMessage()));
                return;
            }
            CompilationUnit cu = parseResult.getResult().get();
            Optional<String> classNameOpt = extractClassName(content);
            if (!classNameOpt.isPresent()) {
                System.err.println("Error: Could not extract class name from " + javaFile);
                return;
            }
            String className = classNameOpt.get();
            List<MethodDeclaration> allMethods = cu.findAll(MethodDeclaration.class);
            if (allMethods.isEmpty()) {
                System.out.println("Warning: No methods found in " + javaFile);
                return;
            }
            // Generate context file
            generateContextFile(className, cu, allMethods);
            // Generate batch prompts
            List<List<MethodDeclaration>> batches = batchMethods(allMethods);
            String batchTemplate = readSectionFromTemplate("PROMPT");
            Path promptsDir = Paths.get(PROMPTS_DIR);
            for (int i = 0; i < batches.size(); i++) {
                List<MethodDeclaration> batch = batches.get(i);
                String prompt = generateBatchPromptFromTemplate(batchTemplate, className, batch);
                String batchFileName = (outputFileName != null && batches.size() == 1)
                    ? outputFileName
                    : className + "_junit_prompt_batch" + (i + 1) + ".txt";
                Path outputPath = promptsDir.resolve(batchFileName);
                Files.write(outputPath, prompt.getBytes("UTF-8"), StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
                System.out.println("Generated prompt for batch " + (i + 1) + " in " + outputPath);
            }
        } catch (IOException e) {
            System.err.println("Error processing file " + javaFile + ": " + e.getMessage());
        }
    }
    
    /**
     * Find all Java files in a directory recursively
     */
    public static List<Path> findAllJavaFiles(String packagePath) {
        List<Path> javaFiles = new ArrayList<>();
        try (Stream<Path> paths = Files.walk(Paths.get(packagePath))) {
            paths.filter(Files::isRegularFile)
                 .filter(path -> path.toString().endsWith(".java"))
                 .forEach(javaFiles::add);
        } catch (IOException e) {
            System.err.println("Error finding Java files: " + e.getMessage());
        }
        return javaFiles;
    }
    
    /**
     * Main method for CLI usage
     */
    public static void main(String[] args) {
        if (args.length < 2) {
            printUsage();
            return;
        }
        
        String packagePath = null;
        String className = null;
        String outputFileName = null;
        
        // Simple argument parsing
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--package":
                    if (i + 1 < args.length) {
                        packagePath = args[++i];
                    }
                    break;
                case "--class":
                    if (i + 1 < args.length) {
                        className = args[++i];
                    }
                    break;
                case "--output":
                    if (i + 1 < args.length) {
                        outputFileName = args[++i];
                    }
                    break;
                default:
                    if (!args[i].startsWith("--")) {
                        System.err.println("Unknown argument: " + args[i]);
                    }
                    break;
            }
        }
        
        if (packagePath == null) {
            System.err.println("Error: --package argument is required");
            printUsage();
            return;
        }
        
        if (className != null) {
            // Process specific class
            Optional<Path> javaFile = findJavaFileByClass(packagePath, className);
            if (!javaFile.isPresent()) {
                System.err.println("Error: Could not find " + className + ".java in " + packagePath);
                return;
            }
            processJavaFile(javaFile.get(), outputFileName);
        } else {
            // Process all Java files in package
            List<Path> javaFiles = findAllJavaFiles(packagePath);
            if (javaFiles.isEmpty()) {
                System.err.println("Error: No Java files found in " + packagePath);
                return;
            }
            
            for (Path javaFile : javaFiles) {
                processJavaFile(javaFile, null);
            }
        }
    }
    
    private static void printUsage() {
        System.out.println("Usage: TestPromptGenerator --package <directory> [--class <className>] [--output <fileName>]");
        System.out.println("  --package: Path to the package (directory) to search");
        System.out.println("  --class:   Name of the Java class (without .java) - optional");
        System.out.println("  --output:  Output file name - optional");
        System.out.println();
        System.out.println("Examples:");
        System.out.println("  Generate prompts for specific class:");
        System.out.println("    --package src/main/java --class MyClass");
        System.out.println("  Generate prompts for all classes in package:");
        System.out.println("    --package src/main/java");
    }
} 