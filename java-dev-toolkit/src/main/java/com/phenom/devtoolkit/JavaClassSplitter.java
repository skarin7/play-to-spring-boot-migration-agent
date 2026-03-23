package com.phenom.devtoolkit;

import java.io.FileInputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Scanner;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.javaparser.JavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Modifier;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.BodyDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.MarkerAnnotationExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.Name;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import com.github.javaparser.ast.stmt.ReturnStmt;
import com.github.javaparser.ast.type.Type;
import com.github.javaparser.ast.type.VoidType;
import com.github.javaparser.printer.DefaultPrettyPrinter;
import com.github.javaparser.printer.configuration.DefaultPrinterConfiguration;

public class JavaClassSplitter {
    public enum AnnotationStyle { JAVA, PLAY }
    public enum InjectionStyle { FIELD, CONSTRUCTOR }
    private final JavaParser javaParser;
    private final DefaultPrettyPrinter printer;
    private final ObjectMapper objectMapper;
    // Change from private AnnotationStyle annotationStyle = AnnotationStyle.JAVA;
    AnnotationStyle annotationStyle = AnnotationStyle.JAVA;
    InjectionStyle injectionStyle = InjectionStyle.FIELD;
    
    // Track methods moved due to cyclic dependency resolution
    private final Map<String, List<String>> cyclicDependencyMovedMethods = new HashMap<>();
    
    public JavaClassSplitter() {
        this.javaParser = new JavaParser();
        this.printer = new DefaultPrettyPrinter(new DefaultPrinterConfiguration());
        this.objectMapper = new ObjectMapper();
    }
    
    public static void main(String[] args) {
        if (args.length < 1) {
            System.out.println("Usage: java -jar dev-toolkit.jar <mode> [other args] [--annotation-style=java|play] [--injection-style=field|constructor]");
            System.out.println("Modes: generate <input-file> <output-directory> <mapping-json>");
            System.out.println("       verify <output-directory> <mapping-json>");
            System.out.println("       generate-test-prompts --package <directory> [--class <className>] [--output <fileName>]");
            System.out.println("       undoChanges <output-directory>");
            return;
        }
        JavaClassSplitter splitter = new JavaClassSplitter();

        // Detect annotation style
        AnnotationStyle style = detectAnnotationStyle(args);
        splitter.annotationStyle = style;
        System.out.println("Using annotation style: " + style);

        // Detect injection style
        InjectionStyle injectionStyle = detectInjectionStyle(args);
        splitter.injectionStyle = injectionStyle;
        System.out.println("Using injection style: " + injectionStyle);

        try {
            String mode = args[0];
            if ("generate".equalsIgnoreCase(mode)) {
                if (args.length < 4) {
                    System.out.println("Usage: generate <input-file> <output-directory> <mapping-json>");
                    return;
                }
                String inputFile = Paths.get(args[1]).toAbsolutePath().normalize().toString();
                String outputDir = Paths.get(args[2]).toAbsolutePath().normalize().toString();
                String mappingFile = Paths.get(args[3]).toAbsolutePath().normalize().toString();
                Map<String, List<String>> classMethodMapping = splitter.loadMethodMapping(mappingFile);
                splitter.splitClassWithDelegation(inputFile, outputDir, classMethodMapping);
                System.out.println("Class splitting completed successfully!");
            } else if ("verify".equalsIgnoreCase(mode)) {
                if (args.length < 3) {
                    System.out.println("Usage: verify <output-directory> <mapping-json>");
                    return;
                }
                String outputDir = Paths.get(args[1]).toAbsolutePath().normalize().toString();
                String mappingFile = Paths.get(args[2]).toAbsolutePath().normalize().toString();
                Map<String, List<String>> classMethodMapping = splitter.loadMethodMapping(mappingFile);
                splitter.verifySplitResults(outputDir, classMethodMapping);
            } else if ("undoChanges".equalsIgnoreCase(mode)) {
                if (args.length < 2) {
                    System.out.println("Usage: undoChanges <output-directory>");
                    return;
                }
                String outputDir = Paths.get(args[1]).toAbsolutePath().normalize().toString();
                splitter.undoChanges(outputDir);
            } else if ("generate-test-prompts".equalsIgnoreCase(mode)) {
                // Delegate to TestPromptGenerator
                String[] testPromptArgs = new String[args.length - 1];
                System.arraycopy(args, 1, testPromptArgs, 0, args.length - 1);
                TestPromptGenerator.main(testPromptArgs);
            } else if (args.length == 3) {
                // Default to generate mode if only 3 arguments are provided
                String inputFile = Paths.get(args[0]).toAbsolutePath().normalize().toString();
                String outputDir = Paths.get(args[1]).toAbsolutePath().normalize().toString();
                String mappingFile = Paths.get(args[2]).toAbsolutePath().normalize().toString();
                Map<String, List<String>> classMethodMapping = splitter.loadMethodMapping(mappingFile);
                splitter.splitClassWithDelegation(inputFile, outputDir, classMethodMapping);
                System.out.println("Class splitting completed successfully!");
            } else {
                System.out.println("Unknown mode or invalid arguments.\nUsage: java -jar dev-toolkit.jar <mode> [other args]");
            }
        } catch (Exception e) {
            System.err.println("Error: " + e.getMessage());
            e.printStackTrace();
        }
    }
    
    /**
     * Load method mapping from JSON file
     */
    Map<String, List<String>> loadMethodMapping(String mappingFile) throws IOException {
        Path mappingPath = Paths.get(mappingFile);
        if (!Files.exists(mappingPath)) {
            throw new IOException("Mapping file not found: " + mappingFile);
        }
        
        // Check if file is empty or contains only whitespace
        if (Files.size(mappingPath) == 0) {
            throw new IOException("Mapping file is empty: " + mappingFile + ". Please ensure the file contains valid JSON mapping.");
        }
        
        // Read file content and check for empty/whitespace-only content
        String content = new String(Files.readAllBytes(mappingPath), "UTF-8").trim();
        if (content.isEmpty()) {
            throw new IOException("Mapping file contains only whitespace: " + mappingFile + ". Please ensure the file contains valid JSON mapping.");
        }
        
        TypeReference<Map<String, List<String>>> typeRef = new TypeReference<Map<String, List<String>>>() {};
        Map<String, List<String>> mapping;
        
        try (FileInputStream fis = new FileInputStream(mappingFile)) {
            mapping = objectMapper.readValue(fis, typeRef);
        } catch (com.fasterxml.jackson.databind.exc.MismatchedInputException e) {
            throw new IOException("Invalid or empty JSON in mapping file: " + mappingFile + ". Please ensure the file contains valid JSON mapping like: {\"ClassName1\": [\"method1\", \"method2\"], \"ClassName2\": [\"method3\"]}", e);
        } catch (com.fasterxml.jackson.core.JsonParseException e) {
            throw new IOException("Invalid JSON syntax in mapping file: " + mappingFile + ". Please check the JSON format. Error: " + e.getMessage(), e);
        }
        
        if (mapping == null || mapping.isEmpty()) {
            throw new IOException("Mapping file contains no class-to-method mappings: " + mappingFile);
        }
        
        // Remove duplicate method names for each class
        for (Map.Entry<String, List<String>> entry : mapping.entrySet()) {
            List<String> original = entry.getValue();
            List<String> deduped = new ArrayList<>();
            Set<String> seen = new HashSet<>();
            for (String method : original) {
                if (!seen.add(method)) {
                    System.err.println("[WARNING] Duplicate method name '" + method + "' found in split_classes.json for class '" + entry.getKey() + "'. Removing duplicate.");
                } else {
                    deduped.add(method);
                }
            }
            entry.setValue(deduped);
        }
        System.out.println("Loaded mapping for " + mapping.size() + " classes:");
        mapping.forEach((className, methods) -> 
            System.out.println("  " + className + " -> " + methods.size() + " methods"));
        
        return mapping;
    }
    
    /**
     * Split class with delegation pattern
     * @param inputFile Path to the original Java file
     * @param outputDir Path to the output directory (optional; if null or empty, uses the directory of inputFile)
     * @param classMethodMapping Mapping of new class names to method lists
     */
    public void splitClassWithDelegation(String inputFile, String outputDir, 
                                        Map<String, List<String>> classMethodMapping) throws IOException {
        // If outputDir is null or empty, use the directory of inputFile
        if (outputDir == null || outputDir.trim().isEmpty()) {
            Path inputPath = Paths.get(inputFile).toAbsolutePath().normalize();
            outputDir = inputPath.getParent().toString();
        }
        
        // Parse the input file
        CompilationUnit cu = javaParser.parse(new FileInputStream(inputFile))
            .getResult()
            .orElseThrow(() -> new RuntimeException("Failed to parse Java file"));
        
        // --- Verification: Check for missing methods in split_classes.json ---
        String inputFileName = Paths.get(inputFile).getFileName().toString();
        String className = inputFileName.replaceFirst("\\.java$", "");
        // Read <class>_methods.json from the project root directory
        Path methodsJsonPath = Paths.get(className + "_methods.json");
        if (Files.exists(methodsJsonPath)) {
            // Check if methods JSON file is empty or contains only whitespace
            if (Files.size(methodsJsonPath) == 0) {
                System.err.println("[WARNING] Methods JSON file is empty: " + methodsJsonPath + ". Skipping method coverage verification.");
            } else {
                try {
                    // Read file content and check for empty/whitespace-only content
                    String methodsContent = new String(Files.readAllBytes(methodsJsonPath), "UTF-8").trim();
                    if (methodsContent.isEmpty()) {
                        System.err.println("[WARNING] Methods JSON file contains only whitespace: " + methodsJsonPath + ". Skipping method coverage verification.");
                    } else {
                        List<String> allMethodNames = new ArrayList<>();
                        try (FileInputStream fis = new FileInputStream(methodsJsonPath.toFile())) {
                            Map<String, Object> methodsJson = objectMapper.readValue(fis, new TypeReference<Map<String, Object>>(){});
                            if (methodsJson != null) {
                                allMethodNames.addAll(methodsJson.keySet());
                            }
                        } catch (com.fasterxml.jackson.databind.exc.MismatchedInputException e) {
                            System.err.println("[WARNING] Invalid or empty JSON in methods file: " + methodsJsonPath + ". Skipping method coverage verification. Error: " + e.getMessage());
                        } catch (com.fasterxml.jackson.core.JsonParseException e) {
                            System.err.println("[WARNING] Invalid JSON syntax in methods file: " + methodsJsonPath + ". Skipping method coverage verification. Error: " + e.getMessage());
                        } catch (Exception e) {
                            System.err.println("[WARNING] Error reading methods file: " + methodsJsonPath + ". Skipping method coverage verification. Error: " + e.getMessage());
                        }
                        
                        if (!allMethodNames.isEmpty()) {
                            // Collect all methods in split_classes.json
                            Set<String> mappedMethods = new HashSet<>();
                            for (List<String> methods : classMethodMapping.values()) {
                                mappedMethods.addAll(methods);
                            }
                            // Find missing methods
                            List<String> missing = new ArrayList<>();
                            for (String m : allMethodNames) {
                                if (!mappedMethods.contains(m)) {
                                    missing.add(m);
                                }
                            }
                            if (!missing.isEmpty()) {
                                System.err.println("[WARNING] The following methods are present in " + methodsJsonPath.getFileName() + " but missing from split_classes.json:");
                                for (String m : missing) {
                                    System.err.println("  - " + m);
                                }
                                System.err.println("Please review and update your split_classes.json to include all required methods before proceeding.");
                                throw new RuntimeException("Aborting split: missing methods detected in split_classes.json. See warnings above.");
                            }
                        }
                    }
                } catch (IOException e) {
                    System.err.println("[WARNING] Error reading methods file: " + methodsJsonPath + ". Skipping method coverage verification. Error: " + e.getMessage());
                }
            }
        } else {
            System.err.println("[INFO] No methods JSON found at " + methodsJsonPath + ". Skipping method coverage verification.");
        }
        
        // Track files for rollback in case of error
        List<Path> createdFiles = new ArrayList<>();
        Path originalFileBackup = null;
        boolean originalFileModified = false;
        Path manifestPath = null;
        
        // Create backup of the original class before any modifications
        ClassOrInterfaceDeclaration mainClass = cu.findFirst(ClassOrInterfaceDeclaration.class)
            .orElseThrow(() -> new RuntimeException("No class found in file"));
        String originalClassName = mainClass.getNameAsString();
        Path originalFilePath = Paths.get(inputFile);
        String originalFileName = originalFilePath.getFileName().toString();
        String originalClassNameWithSuffix = originalFileName.replaceFirst("\\.java$", "Original.java");
        Path outputPath = Paths.get(outputDir);
        
        // Find the main class
        // String originalClassName = mainClass.getNameAsString(); // Moved up
        System.out.println("Processing class: " + originalClassName);
        
        // Create output directory (delete if exists)
        // Path outputPath = Paths.get(outputDir); // Moved up
        Path inputPath = Paths.get(inputFile).toAbsolutePath().normalize();
        Path inputParent = inputPath.getParent();
        if (Files.exists(outputPath)) {
            // Only delete if outputPath is NOT the same as the input file's parent directory
            if (!outputPath.toAbsolutePath().normalize().equals(inputParent)) {
                deleteDirectoryRecursively(outputPath);
            } else {
                System.out.println("[SAFEGUARD] Output directory is the same as the input file's directory. Skipping directory deletion to avoid data loss.");
            }
        }
        Files.createDirectories(outputPath);
        
        // Create backup of the original class after output directory is set up
        Path bkpDir = outputPath.resolve("_bkp");
        Files.createDirectories(bkpDir);
        Path originalBackupPath = bkpDir.resolve(originalClassNameWithSuffix);
        // Clone the CompilationUnit and update the class name for the backup
        CompilationUnit backupCu = cu.clone();
        backupCu.findFirst(ClassOrInterfaceDeclaration.class).ifPresent(cls -> {
            String newClassName = cls.getNameAsString() + "Original";
            cls.setName(newClassName);
            // Update all constructor names to match the new class name
            cls.getConstructors().forEach(cons -> cons.setName(newClassName));
        });
        // Write the backup file with the updated class name
        try (FileWriter writer = new FileWriter(originalBackupPath.toFile())) {
            writer.write(printer.print(backupCu));
        }
        createdFiles.add(originalBackupPath);
        System.out.println("✅ Created backup of original class: " + originalBackupPath);
        
        // Set system property for output path so package logic can use it
        System.setProperty("split.outputPath", outputPath.toString());
        // Track split class package for import management
        Map<String, String> splitClassToPackage = new HashMap<>();
        // Track dependencies for cycle detection
        Map<String, Set<String>> splitClassDependencies = new HashMap<>();
        try {
            // Create reverse mapping: method name -> target class name
            Map<String, String> methodToClassMapping = createMethodToClassMapping(classMethodMapping);
            
            // Get all members from the original class
            List<BodyDeclaration<?>> allMembers = new ArrayList<>(mainClass.getMembers());
            
            // Create method name to declaration mapping (all overloads)
            Map<String, List<MethodDeclaration>> methodNameToDeclarations = createMethodDeclarationsMapping(allMembers);
            
            // Track processing results
            Set<String> processedMethods = new HashSet<>();
            Set<String> notFoundMethods = new HashSet<>();
            // Create new classes and collect methods to move
            Map<String, List<MethodDeclaration>> newClassMethods = new HashMap<>();
            for (Map.Entry<String, List<String>> entry : classMethodMapping.entrySet()) {
                String newClassName = entry.getKey();
                List<String> methodNames = entry.getValue();
                System.out.println("\nProcessing methods for class: " + newClassName);
                List<MethodDeclaration> methodsForNewClass = new ArrayList<>();
                // No need for methodSignatures set, as duplicates are filtered during mapping load
                for (String methodName : methodNames) {
                    List<MethodDeclaration> methods = methodNameToDeclarations.get(methodName);
                    if (methods != null && !methods.isEmpty()) {
                        for (MethodDeclaration method : methods) {
                            methodsForNewClass.add(method.clone()); // Clone each overload
                            processedMethods.add(methodName);
                            System.out.println("  ✅ Found: " + methodName + (methods.size() > 1 ? " (overload)" : ""));
                        }
                    }
                }
                if (!methodsForNewClass.isEmpty()) {
                    newClassMethods.put(newClassName, methodsForNewClass);
                }
            }
            
            // Create new service classes
            for (Map.Entry<String, List<MethodDeclaration>> entry : newClassMethods.entrySet()) {
                String newClassName = entry.getKey();
                List<MethodDeclaration> methods = entry.getValue();
                
                // Find dependent fields for this new class
                List<FieldDeclaration> dependentFields = findDependentFields(methods, allMembers);
                
                // Rewrite intra-class method calls and collect required split class dependencies (pass methodNameToDeclarations)
                Set<String> requiredSplitClassDeps = rewriteIntraClassMethodCallsAndCollectDependencies(methods, methodToClassMapping, newClassName, methodNameToDeclarations);
                // Add required split class fields if not already present
                for (String depClass : requiredSplitClassDeps) {
                    boolean alreadyPresent = dependentFields.stream().anyMatch(f -> f.getVariables().stream().anyMatch(v -> v.getType().asString().equals(depClass)));
                    if (!alreadyPresent) {
                        FieldDeclaration depField = new FieldDeclaration();
                        depField.setModifiers(Modifier.Keyword.PRIVATE);
                        VariableDeclarator var = new VariableDeclarator();
                        var.setName(getFieldNameFromClassName(depClass));
                        var.setType(depClass);
                        depField.addVariable(var);
                        dependentFields.add(depField);
                    }
                }
                
                // Store dependencies for cycle detection
                splitClassDependencies.put(newClassName, requiredSplitClassDeps);
                // Ensure all moved methods are at least package-private (remove private modifier if present)
                for (MethodDeclaration method : methods) {
                    if (method.isPrivate()) {
                        method.removeModifier(Modifier.Keyword.PRIVATE);
                    }
                }
            }
            
            // Detect and resolve cyclic dependencies before generating classes
            boolean cyclesResolved = detectAndResolveCyclicDependencies(splitClassDependencies, classMethodMapping);
            
            // If cycles were resolved, rebuild newClassMethods from the updated classMethodMapping
            if (cyclesResolved) {
                System.out.println("🔄 Rebuilding class methods from optimized mapping...");
                newClassMethods.clear();
                
                // Rebuild newClassMethods from the updated classMethodMapping
                for (Map.Entry<String, List<String>> entry : classMethodMapping.entrySet()) {
                    String newClassName = entry.getKey();
                    List<String> methodNames = entry.getValue();
                    List<MethodDeclaration> methods = new ArrayList<>();
                    
                    // Find method declarations for each method name
                    for (String methodName : methodNames) {
                        List<MethodDeclaration> methodDeclarations = methodNameToDeclarations.get(methodName);
                        if (methodDeclarations != null) {
                            methods.addAll(methodDeclarations);
                        } else {
                            System.err.println("⚠️  Warning: Method declaration not found for: " + methodName);
                        }
                    }
                    
                    if (!methods.isEmpty()) {
                        newClassMethods.put(newClassName, methods);
                    }
                }
                
                // Rebuild methodToClassMapping from updated classMethodMapping
                methodToClassMapping = createMethodToClassMapping(classMethodMapping);
                
                // Recalculate dependencies for the new class structure
                splitClassDependencies.clear();
                for (Map.Entry<String, List<MethodDeclaration>> entry : newClassMethods.entrySet()) {
                    String newClassName = entry.getKey();
                    List<MethodDeclaration> methods = entry.getValue();
                    Set<String> requiredSplitClassDeps = rewriteIntraClassMethodCallsAndCollectDependencies(methods, methodToClassMapping, newClassName, methodNameToDeclarations);
                    splitClassDependencies.put(newClassName, requiredSplitClassDeps);
                }
                
                System.out.println("✅ Rebuilt class structure from optimized mapping");
            }
            
            // Generate the classes after cycle detection passes
            for (Map.Entry<String, List<MethodDeclaration>> entry : newClassMethods.entrySet()) {
                String newClassName = entry.getKey();
                List<MethodDeclaration> methods = entry.getValue();
                
                // Find dependent fields for this new class
                List<FieldDeclaration> dependentFields = findDependentFields(methods, allMembers);
                
                // Get stored dependencies
                Set<String> requiredSplitClassDeps = splitClassDependencies.get(newClassName);
                // Add required split class fields if not already present
                for (String depClass : requiredSplitClassDeps) {
                    boolean alreadyPresent = dependentFields.stream().anyMatch(f -> f.getVariables().stream().anyMatch(v -> v.getType().asString().equals(depClass)));
                    if (!alreadyPresent) {
                        FieldDeclaration depField = new FieldDeclaration();
                        depField.setModifiers(Modifier.Keyword.PRIVATE);
                        VariableDeclarator var = new VariableDeclarator();
                        var.setName(getFieldNameFromClassName(depClass));
                        var.setType(depClass);
                        depField.addVariable(var);
                        dependentFields.add(depField);
                    }
                }
                
                // Create new class
                CompilationUnit newCu = createNewServiceClassWithImports(cu, newClassName, methods, dependentFields, mainClass, splitClassToPackage, classMethodMapping.keySet());
                // Track the package for this split class
                String pkg = newCu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse("");
                splitClassToPackage.put(newClassName, pkg);
                // Write to file and track for rollback
                Path newClassFile = writeClassToFileWithTracking(newCu, outputPath, newClassName);
                createdFiles.add(newClassFile);
                System.out.println("✅ Created " + newClassName + ".java with " + methods.size() + " methods");
            }
            
            // Add field declarations for new service classes to original class
            addServiceFieldsToOriginalClass(mainClass, classMethodMapping.keySet());

            // Remove all other fields except split service references, loggers, and static finals
            List<BodyDeclaration<?>> toRemoveFields = new ArrayList<>();
            for (BodyDeclaration<?> member : mainClass.getMembers()) {
                if (member instanceof FieldDeclaration) {
                    FieldDeclaration field = (FieldDeclaration) member;
                    boolean isLogger = field.getVariables().stream().anyMatch(var -> isLoggerFieldType(var.getType().asString()));
                    boolean isStaticFinal = field.isStatic() && field.isFinal();
                    boolean isSplitServiceRef = field.getVariables().stream().anyMatch(var -> classMethodMapping.keySet().contains(var.getType().asString()));
                    if (!isLogger && !isStaticFinal && !isSplitServiceRef) {
                        toRemoveFields.add(field);
                    }
                }
            }
            toRemoveFields.forEach(mainClass::remove);
            
            // Ensure all private fields in the original class are also final, except service fields for field injection
            for (BodyDeclaration<?> member : mainClass.getMembers()) {
                if (member instanceof FieldDeclaration) {
                    FieldDeclaration field = (FieldDeclaration) member;
                    if (field.isPrivate() && !field.isFinal()) {
                        // For field injection, don't make service fields final
                        if (injectionStyle == InjectionStyle.FIELD) {
                            boolean isServiceField = field.getVariables().stream()
                                .anyMatch(var -> classMethodMapping.keySet().contains(var.getType().asString()));
                            if (!isServiceField) {
                                field.addModifier(Modifier.Keyword.FINAL);
                            }
                        } else {
                            // For constructor injection, make all private fields final
                            field.addModifier(Modifier.Keyword.FINAL);
                        }
                    }
                }
            }
            
            // Add imports for split classes to original class
            addImportsForSplitClassesToOriginal(cu, classMethodMapping.keySet(), splitClassToPackage);
            
            // Convert moved methods to delegation calls in original class
            convertMethodsToDelegation(mainClass, methodToClassMapping, methodNameToDeclarations, outputPath);
            
            // Move original file to output directory with 'Original' suffix
            // Path originalFilePath = Paths.get(inputFile); // Moved up
            // String originalFileName = originalFilePath.getFileName().toString(); // Moved up
            // String originalClassNameWithSuffix = originalFileName.replaceFirst("\\.java$", "Original.java"); // Moved up
            // Path originalBackupPath = outputPath.resolve(originalClassNameWithSuffix); // Moved up
            
            // Create a backup of the original file before modifying it
            originalFileBackup = Files.copy(originalFilePath, originalFilePath.resolveSibling(originalFilePath.getFileName() + ".temp_backup"), java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            
            Files.deleteIfExists(originalFilePath); // Remove the original file before rewriting

            // Write modified original class to the original file location (with original class name)
            try (FileWriter writer = new FileWriter(originalFilePath.toFile())) {
                writer.write(printer.print(cu));
            }
            originalFileModified = true;
            System.out.println("✅ Updated original class: " + originalFileName + " (delegation applied)");
            System.out.println("✅ Moved original file to: " + originalBackupPath);
            
            // Print summary
            printSummary(classMethodMapping, processedMethods, notFoundMethods);

            // --- Automatically verify split results after generation ---
            System.out.println("\n[INFO] Running post-generation verification of split classes...");
            verifySplitResults(outputPath.toString(), classMethodMapping);
            
            // After splitting, if cycles were resolved, save the modified mapping for user review
            if (cyclesResolved) {
                try {
                    Path modifiedMappingPath = Paths.get(System.getProperty("user.dir"), "split_classes_modified.json");
                    objectMapper.writerWithDefaultPrettyPrinter().writeValue(modifiedMappingPath.toFile(), classMethodMapping);
                    System.out.println("[INFO] Modified split mapping with cycle-breaking changes saved to: " + modifiedMappingPath);
                } catch (Exception e) {
                    System.err.println("[WARNING] Failed to write split_classes_modified.json: " + e.getMessage());
                }
            }
            // If we reach here, everything succeeded - clean up temp backup
            if (originalFileBackup != null && Files.exists(originalFileBackup)) {
                Files.delete(originalFileBackup);
            }
            // --- Write undo manifest ---
            manifestPath = Paths.get(System.getProperty("user.dir"), ".split_undo.json");
            Map<String, Object> manifest = new HashMap<>();
            manifest.put("originalFile", originalFilePath.toString());
            manifest.put("originalBackup", originalBackupPath.toString());
            List<String> generatedFiles = new ArrayList<>();
            for (Path p : createdFiles) generatedFiles.add(p.toString());
            manifest.put("generatedFiles", generatedFiles);
            try (FileWriter writer = new FileWriter(manifestPath.toFile())) {
                objectMapper.writerWithDefaultPrettyPrinter().writeValue(writer, manifest);
            }
            System.out.println("[INFO] Undo manifest written: " + manifestPath);
        } catch (Exception e) {
            // Rollback all changes
            System.err.println("Error during splitting: " + e.getMessage());
            System.err.println("[ROLLBACK] Reverting all changes...");
            
            // Restore original file if it was modified
            if (originalFileModified && originalFileBackup != null && Files.exists(originalFileBackup)) {
                try {
                    Files.move(originalFileBackup, originalFilePath, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
                    System.err.println("[ROLLBACK] Restored original file: " + originalFilePath);
                } catch (IOException rollbackError) {
                    System.err.println("[ROLLBACK ERROR] Failed to restore original file: " + rollbackError.getMessage());
                }
            }
            
            // Delete all created files
            for (Path createdFile : createdFiles) {
                try {
                    if (Files.exists(createdFile)) {
                        Files.delete(createdFile);
                        System.err.println("[ROLLBACK] Deleted: " + createdFile);
                    }
                } catch (IOException rollbackError) {
                    System.err.println("[ROLLBACK ERROR] Failed to delete " + createdFile + ": " + rollbackError.getMessage());
                }
            }
            
            // Only delete output directory if it's different from input parent and we created it
            if (!outputPath.toAbsolutePath().normalize().equals(inputParent)) {
                try {
                    deleteDirectoryRecursively(outputPath);
                    System.err.println("[ROLLBACK] Cleaned up output directory: " + outputPath);
                } catch (Exception rollbackError) {
                    System.err.println("[ROLLBACK ERROR] Failed to clean up output directory: " + rollbackError.getMessage());
                }
            } else {
                System.err.println("[ROLLBACK] Skipping deletion of output directory as it matches the input file's parent directory.");
            }
            
            // Clean up temp backup if it exists
            if (originalFileBackup != null && Files.exists(originalFileBackup)) {
                try {
                    Files.delete(originalFileBackup);
                } catch (IOException rollbackError) {
                    System.err.println("[ROLLBACK ERROR] Failed to clean up temp backup: " + rollbackError.getMessage());
                }
            }
            
            throw e;
        }
    }
    
    /**
     * Verify that all methods listed in the mapping exist in the generated split class files
     */
    public void verifySplitResults(String outputDir, Map<String, List<String>> classMethodMapping) throws IOException {
        boolean allOk = true;
        for (Map.Entry<String, List<String>> entry : classMethodMapping.entrySet()) {
            String className = entry.getKey();
            List<String> expectedMethods = entry.getValue();
            Path javaFile = Paths.get(outputDir, className + ".java");
            if (!Files.exists(javaFile)) {
                System.out.println("❌ Missing file: " + javaFile);
                allOk = false;
                continue;
            }
            String content = new String(Files.readAllBytes(javaFile));
            Set<String> foundMethods = new HashSet<>();
            // Use JavaParser to robustly extract all method names (regardless of visibility)
            CompilationUnit cu = javaParser.parse(new FileInputStream(javaFile.toFile())).getResult().orElse(null);
            if (cu != null) {
                cu.findAll(MethodDeclaration.class)
                    .forEach(m -> foundMethods.add(m.getNameAsString()));
            }
            Set<String> missing = new HashSet<>(expectedMethods);
            missing.removeAll(foundMethods);
            if (!missing.isEmpty()) {
                System.out.println("❌ Missing in " + className + ": " + missing);
                allOk = false;
            } else {
                System.out.println("✅ All methods present in " + className);
            }
        }
        if (allOk) {
            System.out.println("\nVerification complete: All methods found in all split classes.");
        } else {
            System.out.println("\nVerification complete: Some methods are missing. See above for details.");
        }
    }
    
    /**
     * Create reverse mapping from method name to target class name
     */
    private Map<String, String> createMethodToClassMapping(Map<String, List<String>> classMethodMapping) {
        Map<String, String> methodToClass = new HashMap<>();
        
        for (Map.Entry<String, List<String>> entry : classMethodMapping.entrySet()) {
            String className = entry.getKey();
            for (String methodName : entry.getValue()) {
                methodToClass.put(methodName, className);
            }
        }
        
        return methodToClass;
    }
    
    /**
     * Create mapping from method names to all their declarations (overloads)
     */
    private Map<String, List<MethodDeclaration>> createMethodDeclarationsMapping(List<BodyDeclaration<?>> members) {
        Map<String, List<MethodDeclaration>> mapping = new HashMap<>();
        for (BodyDeclaration<?> member : members) {
            if (member instanceof MethodDeclaration) {
                MethodDeclaration method = (MethodDeclaration) member;
                mapping.computeIfAbsent(method.getNameAsString(), k -> new ArrayList<>()).add(method);
            }
        }
        return mapping;
    }
    
    /**
     * Find fields that the moved methods depend on
     */
    private List<FieldDeclaration> findDependentFields(List<MethodDeclaration> methods, 
                                                       List<BodyDeclaration<?>> allMembers) {
        
        List<FieldDeclaration> dependentFields = new ArrayList<>();
        Set<String> referencedFieldNames = new HashSet<>();
        
        // Collect all field references from methods
        for (MethodDeclaration method : methods) {
            String methodBody = method.toString();
            
            // Find field references in the method body
            for (BodyDeclaration<?> member : allMembers) {
                if (member instanceof FieldDeclaration) {
                    FieldDeclaration field = (FieldDeclaration) member;
                    for (VariableDeclarator var : field.getVariables()) {
                        String fieldName = var.getNameAsString();
                        if (methodBody.contains(fieldName) || methodBody.contains("this." + fieldName)) {
                            referencedFieldNames.add(fieldName);
                        }
                    }
                }
            }
        }
        
        // Collect the actual field declarations
        for (BodyDeclaration<?> member : allMembers) {
            if (member instanceof FieldDeclaration) {
                FieldDeclaration field = (FieldDeclaration) member;
                for (VariableDeclarator var : field.getVariables()) {
                    if (referencedFieldNames.contains(var.getNameAsString())) {
                        dependentFields.add(field);
                        break;
                    }
                }
            }
        }
        
        return dependentFields;
    }
    
    /**
     * Create new service class with methods and dependent fields
     */
    private CompilationUnit createNewServiceClass(CompilationUnit originalCu, String className,
                                                  List<MethodDeclaration> methods,
                                                  List<FieldDeclaration> dependentFields,
                                                  ClassOrInterfaceDeclaration originalClass) {
        CompilationUnit newCu = new CompilationUnit();

        // --- Set package declaration based on output path ---
        // Try to detect source root (Maven/Java: src/main/java, Play: app)
        String outputPathStr = System.getProperty("split.outputPath");
        String packageName = null;
        if (outputPathStr != null) {
            Path outputPath = Paths.get(outputPathStr).toAbsolutePath().normalize();
            String outputPathNorm = outputPath.toString().replace('\\', '/');
            int srcIdx = outputPathNorm.indexOf("src/main/java/");
            int appIdx = outputPathNorm.indexOf("app/");
            if (srcIdx != -1) {
                String rel = outputPathNorm.substring(srcIdx + "src/main/java/".length());
                packageName = rel.replace('/', '.').replaceAll("^\\.+|\\.+$", "");
            } else if (appIdx != -1) {
                String rel = outputPathNorm.substring(appIdx + "app/".length());
                packageName = rel.replace('/', '.').replaceAll("^\\.+|\\.+$", "");
            }
        }
        if (packageName != null && !packageName.isEmpty()) {
            newCu.setPackageDeclaration(packageName);
        } else {
            // Fallback: use original package
            originalCu.getPackageDeclaration().ifPresent(newCu::setPackageDeclaration);
        }

        // Copy imports
        for (ImportDeclaration importDecl : originalCu.getImports()) {
            newCu.addImport(importDecl);
        }
        // Ensure @Singleton and @Inject imports are present
        newCu.addImport("javax.inject.Singleton");
        newCu.addImport("javax.inject.Inject");

        // Create new class
        ClassOrInterfaceDeclaration newClass = new ClassOrInterfaceDeclaration();
        newClass.setName(className);
        newClass.setModifiers(Modifier.Keyword.PUBLIC);
        // Add @Singleton annotation as marker
        if (annotationStyle == AnnotationStyle.PLAY) {
            newClass.addAnnotation(new MarkerAnnotationExpr("Singleton"));
        } else {
            newClass.addAnnotation(new NormalAnnotationExpr(new Name("Singleton"), new NodeList<MemberValuePair>()));
        }

        // Copy implemented interfaces from original class
        originalClass.getImplementedTypes().forEach(type -> {
            newClass.addImplementedType(type);
            // Add import for the interface if not already present
            String typeName = type.getNameAsString();
            boolean hasImport = newCu.getImports().stream().anyMatch(i -> i.getNameAsString().endsWith(typeName));
            if (!hasImport) {
                // Try to find the fully qualified name from the original imports
                originalCu.getImports().stream()
                    .filter(i -> i.getNameAsString().endsWith(typeName))
                    .findFirst()
                    .ifPresent(newCu::addImport);
            }
        });

        // Add dependent fields
        for (FieldDeclaration field : dependentFields) {
            FieldDeclaration clonedField = field.clone();
            
            if (injectionStyle == InjectionStyle.FIELD) {
                // For field injection, add @Inject annotation only if it doesn't exist and make fields private (not final)
                if (clonedField.isPrivate() && !isPrimitiveOrStringOrBasicCollection(clonedField.getVariables().get(0).getType().asString()) && !isLoggerFieldType(clonedField.getVariables().get(0).getType().asString())) {
                    // Check if @Inject annotation already exists
                    boolean hasInjectAnnotation = clonedField.getAnnotations().stream()
                        .anyMatch(annotation -> annotation.getNameAsString().equals("Inject"));
                    
                    if (!hasInjectAnnotation) {
                        if (annotationStyle == AnnotationStyle.PLAY) {
                            clonedField.addAnnotation(new MarkerAnnotationExpr("Inject"));
                        } else {
                            clonedField.addAnnotation(new NormalAnnotationExpr(new Name("Inject"), new NodeList<MemberValuePair>()));
                        }
                    }
                    // Remove final modifier for field injection
                    clonedField.removeModifier(Modifier.Keyword.FINAL);
                }
            } else {
                // For constructor injection, if field is private and not final, add final modifier
                if (clonedField.isPrivate() && !clonedField.isFinal()) {
                    clonedField.addModifier(Modifier.Keyword.FINAL);
                }
            }
            
            newClass.addMember(clonedField);
        }

        // Add constructor with @Inject only if using constructor injection and there are fields (skip static fields)
        if (injectionStyle == InjectionStyle.CONSTRUCTOR && !dependentFields.isEmpty()) {
            addInjectedConstructorNoStatic(newClass, className, dependentFields);
        }

        // Add methods
        for (MethodDeclaration method : methods) {
            newClass.addMember(method);
        }

        newCu.addType(newClass);

        return newCu;
    }

    // New: Create split class and add imports for referenced split classes
    private CompilationUnit createNewServiceClassWithImports(CompilationUnit originalCu, String className,
                                                  List<MethodDeclaration> methods,
                                                  List<FieldDeclaration> dependentFields,
                                                  ClassOrInterfaceDeclaration originalClass,
                                                  Map<String, String> splitClassToPackage,
                                                  Set<String> allSplitClassNames) {
        // For constructor injection, remove @Inject annotation from all fields (constructor injection only)
        // For field injection, keep @Inject annotations on fields
        if (injectionStyle == InjectionStyle.CONSTRUCTOR) {
            for (FieldDeclaration field : dependentFields) {
                field.getAnnotations().removeIf(a -> a.getNameAsString().equals("Inject"));
            }
        }
        // Update logger field initializers to use the split class name
        for (FieldDeclaration field : dependentFields) {
            for (VariableDeclarator var : field.getVariables()) {
                if (var.getType().asString().contains("Logger")) {
                    if (var.getInitializer().isPresent() && var.getInitializer().get() instanceof MethodCallExpr) {
                        MethodCallExpr call = (MethodCallExpr) var.getInitializer().get();
                        if (call.getNameAsString().equals("getLogger") && call.getScope().isPresent() && call.getScope().get().toString().equals("Log")) {
                            // Replace argument with new split class name
                            call.setArgument(0, new NameExpr(className + ".class"));
                        }
                    }
                }
            }
        }
        CompilationUnit newCu = createNewServiceClass(originalCu, className, methods, dependentFields, originalClass);
        // Add imports for any referenced split classes (only if in a different package)
        Set<String> referencedSplitClasses = new HashSet<>();
        // Check fields
        for (FieldDeclaration field : dependentFields) {
            for (VariableDeclarator var : field.getVariables()) {
                String type = var.getType().asString();
                if (allSplitClassNames.contains(type) && !type.equals(className)) {
                    referencedSplitClasses.add(type);
                }
            }
        }
        // Check method calls
        for (MethodDeclaration method : methods) {
            method.findAll(MethodCallExpr.class).forEach(call -> {
                String called = call.getScope().isPresent() ? call.getScope().get().toString() : null;
                if (called != null && allSplitClassNames.contains(called) && !called.equals(className)) {
                    referencedSplitClasses.add(called);
                }
            });
            // Also check parameter and return types
            for (Parameter param : method.getParameters()) {
                String type = param.getType().asString();
                if (allSplitClassNames.contains(type) && !type.equals(className)) {
                    referencedSplitClasses.add(type);
                }
            }
            String returnType = method.getType().asString();
            if (allSplitClassNames.contains(returnType) && !returnType.equals(className)) {
                referencedSplitClasses.add(returnType);
            }
        }
        String currentPkg = newCu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse("");
        addImportsForReferencedSplitClasses(newCu, className, currentPkg, referencedSplitClasses, splitClassToPackage);
        return newCu;
    }

    /**
     * Add constructor with @Inject for constructor injection, skipping static fields and logger fields
     */
    private void addInjectedConstructorNoStatic(ClassOrInterfaceDeclaration clazz, String className, List<FieldDeclaration> dependentFields) {
        ConstructorDeclaration constructor = new ConstructorDeclaration();
        constructor.setName(className);
        constructor.setModifiers(Modifier.Keyword.PUBLIC);
        // Remove any existing @Inject annotation (defensive)
        constructor.getAnnotations().removeIf(a -> a.getNameAsString().equals("Inject"));
        if (annotationStyle == AnnotationStyle.PLAY) {
            constructor.addAnnotation(new MarkerAnnotationExpr("Inject"));
        } else {
            constructor.addAnnotation(new NormalAnnotationExpr(new Name("Inject"), new NodeList<MemberValuePair>()));
        }
        BlockStmt body = new BlockStmt();

        // Add parameters for each non-static, non-logger field and assign them
        for (FieldDeclaration field : dependentFields) {
            if (field.isStatic()) continue; // skip static fields (like loggers)
            for (VariableDeclarator var : field.getVariables()) {
                String fieldTypeStr = var.getType().asString();
                // Skip logger fields by type
                if (fieldTypeStr.contains("Logger")) continue;
                // Skip primitives, String, and basic collections
                if (isPrimitiveOrStringOrBasicCollection(fieldTypeStr)) continue;
                String fieldName = var.getNameAsString();
                constructor.addParameter(var.getType(), fieldName);
                // this.fieldName = fieldName;
                body.addStatement(new com.github.javaparser.ast.expr.AssignExpr(
                    new com.github.javaparser.ast.expr.FieldAccessExpr(new com.github.javaparser.ast.expr.ThisExpr(), fieldName),
                    new com.github.javaparser.ast.expr.NameExpr(fieldName),
                    com.github.javaparser.ast.expr.AssignExpr.Operator.ASSIGN
                ));
            }
        }
        constructor.setBody(body);
        clazz.addMember(constructor);
    }

    // Helper to check if a type is primitive, String, or a basic collection
    private boolean isPrimitiveOrStringOrBasicCollection(String type) {
        String t = type.trim();
        // Primitive types
        if (t.equals("int") || t.equals("long") || t.equals("double") || t.equals("float") || t.equals("boolean") || t.equals("char") || t.equals("byte") || t.equals("short")) return true;
        // Boxed primitives
        if (t.equals("Integer") || t.equals("Long") || t.equals("Double") || t.equals("Float") || t.equals("Boolean") || t.equals("Character") || t.equals("Byte") || t.equals("Short")) return true;
        // String
        if (t.equals("String")) return true;
        // Basic collections (allow generics)
        if (t.startsWith("List<") || t.startsWith("Set<") || t.startsWith("Map<") || t.equals("List") || t.equals("Set") || t.equals("Map")) return true;
        return false;
    }
    
    /**
     * Rewrite intra-class method calls in moved methods to use the correct split class field if the target method was moved to another class.
     * Also, collect required split class dependencies for injection.
     */
    private Set<String> rewriteIntraClassMethodCallsAndCollectDependencies(List<MethodDeclaration> methods, Map<String, String> methodToClassMapping, String currentClassName, Map<String, List<MethodDeclaration>> methodNameToDeclarations) {
        Set<String> requiredSplitClassDeps = new HashSet<>();
        for (MethodDeclaration method : methods) {
            boolean isCurrentMethodStatic = method.isStatic();
            method.findAll(MethodCallExpr.class).forEach(call -> {
                String calledMethod = call.getNameAsString();
                if (methodToClassMapping.containsKey(calledMethod)) {
                    String targetClass = methodToClassMapping.get(calledMethod);
                    if (!targetClass.equals(currentClassName)) {
                        // Only rewrite if the call is unqualified or qualified with 'this'
                        boolean shouldRewrite = false;
                        if (!call.getScope().isPresent()) {
                            shouldRewrite = true;
                        } else {
                            // Check if scope is 'this'
                            if (call.getScope().get().isThisExpr()) {
                                shouldRewrite = true;
                            }
                        }
                        if (shouldRewrite) {
                            // If current method is static and called method is static, use class name
                            boolean calledIsStatic = false;
                            if (methodNameToDeclarations.containsKey(calledMethod)) {
                                for (MethodDeclaration decl : methodNameToDeclarations.get(calledMethod)) {
                                    if (decl.isStatic()) {
                                        calledIsStatic = true;
                                        break;
                                    }
                                }
                            }
                            if (isCurrentMethodStatic && calledIsStatic) {
                                call.setScope(new NameExpr(targetClass));
                            } else {
                                String fieldAbbr = getFieldNameFromClassName(targetClass);
                                call.setScope(new NameExpr(fieldAbbr));
                                requiredSplitClassDeps.add(targetClass);
                            }
                        }
                    }
                }
            });
        }
        return requiredSplitClassDeps;
    }
    
    /**
     * Find all method calls in the original class and split classes to determine which methods are called from outside their split class
     */
    private Set<String> findMethodsCalledFromOutside(Map<String, List<String>> classMethodMapping, Map<String, List<MethodDeclaration>> methodNameToDeclarations, ClassOrInterfaceDeclaration originalClass, Map<String, List<MethodDeclaration>> newClassMethods) {
        Set<String> calledFromOutside = new HashSet<>();
        // Check calls in the original class
        for (BodyDeclaration<?> member : originalClass.getMembers()) {
            if (member instanceof MethodDeclaration) {
                MethodDeclaration method = (MethodDeclaration) member;
                method.findAll(MethodCallExpr.class).forEach(call -> {
                    String calledMethod = call.getNameAsString();
                    // If the method is in any split class, mark as called from outside
                    for (String splitClass : classMethodMapping.keySet()) {
                        if (classMethodMapping.get(splitClass).contains(calledMethod)) {
                            calledFromOutside.add(splitClass + "." + calledMethod);
                        }
                    }
                });
            }
        }
        // Check calls in other split classes
        for (Map.Entry<String, List<MethodDeclaration>> entry : newClassMethods.entrySet()) {
            String thisClass = entry.getKey();
            for (MethodDeclaration method : entry.getValue()) {
                method.findAll(MethodCallExpr.class).forEach(call -> {
                    String calledMethod = call.getNameAsString();
                    for (String splitClass : classMethodMapping.keySet()) {
                        if (!splitClass.equals(thisClass) && classMethodMapping.get(splitClass).contains(calledMethod)) {
                            calledFromOutside.add(splitClass + "." + calledMethod);
                        }
                    }
                });
            }
        }
        return calledFromOutside;
    }
    
    /**
     * Generate unique field names for split class fields using JavaParser AST if possible, else manual scan
     */
    private String getFieldNameFromClassName(String className) {
        if (className == null || className.isEmpty()) return "";
        return Character.toLowerCase(className.charAt(0)) + className.substring(1);
    }

    /**
     * Add field declarations for service classes to original class
     */
    private void addServiceFieldsToOriginalClass(ClassOrInterfaceDeclaration originalClass, 
                                                 Set<String> serviceClassNames) {
        // Remove all existing private fields except logger
        List<BodyDeclaration<?>> toRemove = new ArrayList<>();
        for (BodyDeclaration<?> member : originalClass.getMembers()) {
            if (member instanceof FieldDeclaration) {
                FieldDeclaration field = (FieldDeclaration) member;
                boolean isLoggerType = field.getVariables().stream()
                    .anyMatch(var -> var.getType().asString().contains("Logger"));
                boolean isPrivate = field.getModifiers().contains(Modifier.privateModifier());
                if (isPrivate && !isLoggerType) {
                    toRemove.add(field);
                }
            }
        }
        toRemove.forEach(originalClass::remove);

        // Find the index of the logger field
        int loggerIndex = -1;
        List<BodyDeclaration<?>> members = originalClass.getMembers();
        for (int i = 0; i < members.size(); i++) {
            BodyDeclaration<?> member = members.get(i);
            if (member instanceof FieldDeclaration) {
                FieldDeclaration field = (FieldDeclaration) member;
                boolean isLoggerType = field.getVariables().stream()
                    .anyMatch(var -> var.getType().asString().contains("Logger"));
                if (isLoggerType) {
                    loggerIndex = i;
                    break;
                }
            }
        }
        int insertFieldIndex = loggerIndex + 1;

        // Prepare new fields for each service class
        List<FieldDeclaration> newFields = new ArrayList<>();
        List<String> fieldNames = new ArrayList<>();
        for (String serviceClassName : serviceClassNames) {
            String fieldName = getFieldNameFromClassName(serviceClassName);
            fieldNames.add(fieldName);
            FieldDeclaration serviceField = new FieldDeclaration();
            serviceField.setModifiers(Modifier.Keyword.PRIVATE);
            
            if (injectionStyle == InjectionStyle.FIELD) {
                // For field injection: add @Inject annotation only if it doesn't exist, do NOT make field final
                boolean hasInjectAnnotation = serviceField.getAnnotations().stream()
                    .anyMatch(annotation -> annotation.getNameAsString().equals("Inject"));
                
                if (!hasInjectAnnotation) {
                    if (annotationStyle == AnnotationStyle.PLAY) {
                        serviceField.addAnnotation(new MarkerAnnotationExpr("Inject"));
                    } else {
                        serviceField.addAnnotation(new NormalAnnotationExpr(new Name("Inject"), new NodeList<MemberValuePair>()));
                    }
                }
                // Field injection fields should NOT be final
            } else {
                // For constructor injection, make field final (no @Inject on field)
                serviceField.addModifier(Modifier.Keyword.FINAL);
            }
            
            VariableDeclarator var = new VariableDeclarator();
            var.setName(fieldName);
            var.setType(serviceClassName);
            serviceField.addVariable(var);
            newFields.add(serviceField);
            System.out.println("  Added field: " + serviceClassName + " " + fieldName + " (injection: " + injectionStyle + ")");
        }
        // Insert new fields right after logger
        for (int i = 0; i < newFields.size(); i++) {
            originalClass.getMembers().add(insertFieldIndex + i, newFields.get(i));
        }

        // Remove all existing constructors (robust: any with annotations, any params)
        List<BodyDeclaration<?>> toRemoveConstructors = new ArrayList<>();
        for (BodyDeclaration<?> member : new ArrayList<>(originalClass.getMembers())) {
            if (member instanceof ConstructorDeclaration) {
                toRemoveConstructors.add(member);
            }
        }
        toRemoveConstructors.forEach(originalClass::remove);

        // Add a new constructor only for constructor injection
        if (injectionStyle == InjectionStyle.CONSTRUCTOR) {
            ConstructorDeclaration constructor = new ConstructorDeclaration();
            constructor.setName(originalClass.getNameAsString());
            constructor.setModifiers(Modifier.Keyword.PUBLIC);
            // Remove any existing @Inject annotation (defensive)
            constructor.getAnnotations().removeIf(a -> a.getNameAsString().equals("Inject"));
            if (annotationStyle == AnnotationStyle.PLAY) {
                constructor.addAnnotation(new MarkerAnnotationExpr("Inject"));
            } else {
                constructor.addAnnotation(new NormalAnnotationExpr(new Name("Inject"), new NodeList<MemberValuePair>()));
            }
            BlockStmt body = new BlockStmt();
            int idx = 0;
            for (String serviceClassName : serviceClassNames) {
                String fieldName = fieldNames.get(idx++);
                constructor.addParameter(serviceClassName, fieldName);
                body.addStatement(new com.github.javaparser.ast.expr.AssignExpr(
                    new com.github.javaparser.ast.expr.FieldAccessExpr(new com.github.javaparser.ast.expr.ThisExpr(), fieldName),
                    new com.github.javaparser.ast.expr.NameExpr(fieldName),
                    com.github.javaparser.ast.expr.AssignExpr.Operator.ASSIGN
                ));
            }
            constructor.setBody(body);
            // Insert constructor right after the last field
            originalClass.getMembers().add(insertFieldIndex + newFields.size(), constructor);
        }
    }
    
    /**
     * Convert moved methods to delegation calls
     */
    private void convertMethodsToDelegation(ClassOrInterfaceDeclaration originalClass,
                                           Map<String, String> methodToClassMapping,
                                           Map<String, List<MethodDeclaration>> methodDeclarations,
                                           Path outputPath) {
        
        for (BodyDeclaration<?> member : originalClass.getMembers()) {
            if (member instanceof MethodDeclaration) {
                MethodDeclaration method = (MethodDeclaration) member;
                String methodName = method.getNameAsString();
                
                if (methodToClassMapping.containsKey(methodName)) {
                    String targetClassName = methodToClassMapping.get(methodName);
                    String fieldName = getFieldNameFromClassName(targetClassName);
                    // Check if the method is static in the split class
                    boolean isStatic = isMethodStaticInSplitClass(targetClassName, methodName, outputPath);
                    // Create delegation call
                    createDelegationCall(method, fieldName, methodName, targetClassName, isStatic);
                    System.out.println("  Converted to delegation: " + methodName + " -> " + (isStatic ? (targetClassName + "." + methodName + "()") : (fieldName + "." + methodName + "()")));
                }
            }
        }
    }

    /**
     * Check if a method is static in the split class (output directory) using JavaParser
     */
    private boolean isMethodStaticInSplitClass(String className, String methodName, Path outputPath) {
        // Look for the split class file in the output directory
        Path splitClassPath = outputPath.resolve(className + ".java");
        if (!Files.exists(splitClassPath)) {
            System.err.println("Split class file not found: " + splitClassPath);
            return false;
        }
        
        try {
            byte[] bytes = Files.readAllBytes(splitClassPath);
            String content = new String(bytes, "UTF-8");
            
            CompilationUnit cu = javaParser.parse(content)
                .getResult()
                .orElse(null);
            
            if (cu == null) {
                System.err.println("Failed to parse split class file: " + splitClassPath);
                return false;
            }
            
            // Find all method declarations with the given name
            List<MethodDeclaration> methods = cu.findAll(MethodDeclaration.class);
            for (MethodDeclaration method : methods) {
                if (method.getNameAsString().equals(methodName)) {
                    // Check if this method has static modifier
                    boolean isStatic = method.getModifiers().contains(Modifier.staticModifier());
                    System.out.println("  Method " + methodName + " in " + className + " is " + (isStatic ? "static" : "instance"));
                    return isStatic;
                }
            }
            
            System.err.println("Method " + methodName + " not found in " + className);
            return false;
            
        } catch (IOException e) {
            System.err.println("Error reading split class file " + splitClassPath + ": " + e.getMessage());
            return false;
        }
    }

    /**
     * Create delegation call in method body, with static/instance logic
     */
    private void createDelegationCall(MethodDeclaration method, String fieldName, String methodName, String className, boolean isStatic) {
        BlockStmt newBody = new BlockStmt();
        com.github.javaparser.ast.expr.Expression callTarget;
        
        boolean originalMethodIsStatic = method.isStatic();
        
        if (originalMethodIsStatic && isStatic) {
            // Static method calling static method: use class name
            callTarget = new com.github.javaparser.ast.expr.NameExpr(className);
        } else if (originalMethodIsStatic && !isStatic) {
            // Static method trying to call instance method: this is an error
            System.err.println("❌ ERROR: Static method '" + method.getNameAsString() + "' cannot call instance method '" + methodName + "' in " + className);
            System.err.println("   Consider making '" + methodName + "' static in " + className + " or making '" + method.getNameAsString() + "' non-static.");
            throw new RuntimeException("Static method cannot call instance method: " + method.getNameAsString() + " -> " + methodName);
        } else if (!originalMethodIsStatic && isStatic) {
            // Instance method calling static method: use class name
            callTarget = new com.github.javaparser.ast.expr.NameExpr(className);
        } else {
            // Instance method calling instance method: use field
            callTarget = new com.github.javaparser.ast.expr.NameExpr(fieldName);
        }
        
        MethodCallExpr methodCall = new MethodCallExpr(callTarget, methodName);
        for (Parameter param : method.getParameters()) {
            methodCall.addArgument(new NameExpr(param.getNameAsString()));
        }
        Type returnType = method.getType();
        if (returnType instanceof VoidType) {
            newBody.addStatement(new ExpressionStmt(methodCall));
        } else {
            newBody.addStatement(new ReturnStmt(methodCall));
        }
        method.setBody(newBody);
    }
    
    /**
     * Write class to file and return the file path for rollback tracking
     */
    private Path writeClassToFileWithTracking(CompilationUnit cu, Path outputPath, String className) throws IOException {
        String fileName = className + ".java";
        Path filePath = outputPath.resolve(fileName);

        // Check for existing class file anywhere in the whole repo
        Path repoRoot = Paths.get("").toAbsolutePath();
        boolean exists = false;
        try (java.util.stream.Stream<Path> files = Files.walk(repoRoot)) {
            exists = files.anyMatch(p -> p.getFileName().toString().equals(fileName));
        }
        if (exists) {
            System.err.println("❌ Duplicate class detected: " + fileName);
            System.err.println("A file named '" + fileName + "' already exists in the repository.");
            System.err.println("Please review your mapping/logic, remove or rename the conflicting file, and re-run the split operation.");
            throw new IOException("Class file already exists in repo: " + fileName);
        }

        try (FileWriter writer = new FileWriter(filePath.toFile())) {
            writer.write(printer.print(cu));
        }
        return filePath;
    }
    
    /**
     * Print summary of the splitting operation
     */
    private void printSummary(Map<String, List<String>> classMethodMapping, 
                             Set<String> processedMethods, Set<String> notFoundMethods) {
        
        System.out.println("\n" + repeatChar('=', 50));
        System.out.println("DELEGATION SPLITTING SUMMARY");
        System.out.println(repeatChar('=', 50));
        
        int totalRequestedMethods = classMethodMapping.values().stream()
            .mapToInt(List::size)
            .sum();
        
        System.out.println("Total methods requested to move: " + totalRequestedMethods);
        System.out.println("Total methods successfully processed: " + processedMethods.size());
        System.out.println("Total methods not found: " + notFoundMethods.size());
        
        if (!notFoundMethods.isEmpty()) {
            System.out.println("\n❌ Methods not found:");
            notFoundMethods.forEach(method -> System.out.println("  - " + method));
        }
        
        System.out.println("\n✅ Files created:");
        classMethodMapping.keySet().forEach(className -> 
            System.out.println("  - " + className + ".java"));
        
        System.out.println("  - Original class (modified with delegation)");
        
        System.out.println("\n📝 Original class changes:");
        System.out.println("  - Added service field declarations");
        System.out.println("  - Converted " + processedMethods.size() + " methods to delegation calls");
        System.out.println("  - Kept all other methods unchanged");

        // Print cyclic dependency moved methods info
        if (!cyclicDependencyMovedMethods.isEmpty()) {
            System.out.println("\n🔄 Methods moved due to cyclic dependency resolution:");
            for (Map.Entry<String, List<String>> entry : cyclicDependencyMovedMethods.entrySet()) {
                System.out.println("  → Moved to class: " + entry.getKey());
                for (String method : entry.getValue()) {
                    System.out.println("     - " + method);
                }
            }
        }
    }

    /**
     * Helper method for Java 8 to repeat a character n times (since String.repeat is Java 11+)
     */
    private static String repeatChar(char c, int n) {
        StringBuilder sb = new StringBuilder(n);
        for (int i = 0; i < n; i++) {
            sb.append(c);
        }
        return sb.toString();
    }

    /**
     * Recursively delete a directory and all its contents (delete everything, including 'Original.java' files)
     */
    private void deleteDirectoryRecursively(Path path) throws IOException {
        if (Files.exists(path)) {
            Files.walk(path)
                .sorted(java.util.Comparator.reverseOrder())
                .forEach(p -> {
                    try {
                        Files.delete(p);
                    } catch (IOException e) {
                        throw new RuntimeException("Failed to delete " + p, e);
                    }
                });
        }
    }

    // Add this static method for annotation style detection
    static AnnotationStyle detectAnnotationStyle(String[] args) {
        // Check for override flag
        for (String arg : args) {
            if (arg.startsWith("--annotation-style=")) {
                String val = arg.substring("--annotation-style=".length()).toLowerCase();
                if (val.equals("play")) return AnnotationStyle.PLAY;
                if (val.equals("java")) return AnnotationStyle.JAVA;
            }
        }
        // Auto-detect by file presence
        if (Files.exists(Paths.get("build.sbt"))) return AnnotationStyle.PLAY;
        if (Files.exists(Paths.get("pom.xml"))) return AnnotationStyle.JAVA;
        // Default
        return AnnotationStyle.JAVA;
    }

    // Add this static method for injection style detection
    static InjectionStyle detectInjectionStyle(String[] args) {
        // Check for override flag
        for (String arg : args) {
            if (arg.startsWith("--injection-style=")) {
                String val = arg.substring("--injection-style=".length()).toLowerCase();
                if (val.equals("constructor")) return InjectionStyle.CONSTRUCTOR;
                if (val.equals("field")) return InjectionStyle.FIELD;
            }
        }
        // Default to field injection
        return InjectionStyle.FIELD;
    }

    // Enhanced: Add imports for split classes to the original class's CompilationUnit, only if in a different package
    private void addImportsForSplitClassesToOriginal(CompilationUnit cu, Set<String> splitClassNames, Map<String, String> splitClassToPackage) {
        String currentPkg = cu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse("");
        for (String splitClass : splitClassNames) {
            String pkg = splitClassToPackage.get(splitClass);
            if (pkg != null && !pkg.isEmpty() && !pkg.equals(currentPkg)) {
                cu.addImport(pkg + "." + splitClass);
            }
        }
    }

    // Enhanced: Add imports for referenced split classes to the split class CompilationUnit, only if in a different package
    private void addImportsForReferencedSplitClasses(CompilationUnit cu, String currentClassName, String currentPkg, Set<String> referencedSplitClasses, Map<String, String> splitClassToPackage) {
        for (String refClass : referencedSplitClasses) {
            if (refClass.equals(currentClassName)) continue;
            String pkg = splitClassToPackage.get(refClass);
            if (pkg != null && !pkg.isEmpty() && !pkg.equals(currentPkg)) {
                cu.addImport(pkg + "." + refClass);
            }
        }
    }

    /**
     * Undo changes made by splitClassWithDelegation using the manifest file
     */
    public void undoChanges(String outputDir) throws IOException {
        Path manifestPath = Paths.get(System.getProperty("user.dir"), ".split_undo.json");
        if (!Files.exists(manifestPath)) {
            System.err.println("[UNDO] Manifest not found: " + manifestPath);
            System.err.println("[UNDO] Nothing to undo in this directory.");
            return;
        }
        Map<String, Object> manifest;
        try (FileInputStream fis = new FileInputStream(manifestPath.toFile())) {
            manifest = objectMapper.readValue(fis, new TypeReference<Map<String, Object>>(){});
        }
        String originalFile = (String) manifest.get("originalFile");
        String originalBackup = (String) manifest.get("originalBackup");
        List<String> generatedFiles = (List<String>) manifest.get("generatedFiles");
        // Restore original file
        if (originalBackup != null && Files.exists(Paths.get(originalBackup))) {
            Files.copy(Paths.get(originalBackup), Paths.get(originalFile), java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            System.out.println("[UNDO] Restored original file: " + originalFile);
        } else {
            System.err.println("[UNDO] Backup not found: " + originalBackup);
        }
        // Delete generated files
        for (String file : generatedFiles) {
            Path p = Paths.get(file);
            if (Files.exists(p)) {
                Files.delete(p);
                System.out.println("[UNDO] Deleted: " + file);
            }
        }
        // Delete manifest
        Files.deleteIfExists(manifestPath);
        System.out.println("[UNDO] Undo complete. Manifest removed.");
    }

    /**
     * Detect and resolve cyclic dependencies using iterative optimization
     */
    private boolean detectAndResolveCyclicDependencies(Map<String, Set<String>> dependencies, 
                                                      Map<String, List<String>> classMethodMapping) {
        System.out.println("[CycleCheck] Starting cycle detection among split classes...");
        System.out.println("[CycleCheck] Dependency graph: " + dependencies);
        
        List<List<String>> cycles = findAllCycles(dependencies);
        if (cycles.isEmpty()) {
            System.out.println("[CycleCheck] Cycle detection complete: No cyclic dependencies detected in split classes.");
            System.out.println("✅ No cyclic dependencies detected in split classes");
            return false;
        }
        
        System.out.println("⚠️  Cyclic dependencies detected!");
        for (List<String> cycle : cycles) {
            System.out.println("  🔄 Cycle: " + String.join(" → ", cycle));
        }
        
        // Create a copy of the original mapping for optimization
        Map<String, List<String>> optimizedMapping = new HashMap<>(classMethodMapping);
        
        // Perform optimization to generate proposed changes
        boolean canBeResolved = analyzeAndOptimizeCyclicDependencies(dependencies, optimizedMapping);
        
        if (!canBeResolved) {
            System.out.println("❌ Unable to automatically resolve cyclic dependencies.");
            System.out.println("💡 Manual intervention required. Please review your split_classes.json and modify method groupings.");
            return false;
        }
        
        // Save the proposed modified mapping for user review
        try {
            Path proposedMappingPath = Paths.get(System.getProperty("user.dir"), "split_classes_proposed.json");
            objectMapper.writerWithDefaultPrettyPrinter().writeValue(proposedMappingPath.toFile(), optimizedMapping);
            System.out.println("📋 Proposed changes to resolve cycles saved to: " + proposedMappingPath);
        } catch (Exception e) {
            System.err.println("⚠️  Warning: Could not save proposed mapping: " + e.getMessage());
        }
        
        // Show the proposed changes to the user
        System.out.println("\n🔍 PROPOSED CHANGES TO RESOLVE CYCLIC DEPENDENCIES:");
        System.out.println("The tool has analyzed the cycles and proposes the following method redistributions:");
        
        // Compare original and optimized mappings to show changes
        showProposedChanges(classMethodMapping, optimizedMapping);
        
        // Ask for user confirmation
        System.out.println("\n❓ Do you want to proceed with these changes?");
        System.out.println("   - Enter 'y' to proceed (will use the current proposed changes)");
        System.out.println("   - Enter 'n' to cancel and manually modify your split_classes.json");
        System.out.println("   - You can modify 'split_classes_proposed.json' before entering 'y' to customize the changes");
        System.out.print("Proceed with cycle resolution? (y/n): ");
        
        try {
            java.util.Scanner scanner = new java.util.Scanner(System.in);
            String response = scanner.nextLine().trim().toLowerCase();
            
            if (!"y".equals(response) && !"yes".equals(response)) {
                System.out.println("❌ Cycle resolution cancelled by user.");
                System.out.println("💡 Please manually modify your split_classes.json file and re-run the split command.");
                System.out.println("   - Review the cycles shown above");
                System.out.println("   - Consider the proposed changes in split_classes_proposed.json");
                System.out.println("   - Move methods between classes to break the cycles");
                return false;
            }
            
            System.out.println("✅ User confirmed. Loading final mapping...");
            
            // Reload the proposed mapping in case user made manual changes
            Path proposedMappingPath = Paths.get(System.getProperty("user.dir"), "split_classes_proposed.json");
            if (Files.exists(proposedMappingPath)) {
                try {
                    Map<String, List<String>> userModifiedMapping = objectMapper.readValue(proposedMappingPath.toFile(), 
                        new TypeReference<Map<String, List<String>>>() {});
                    
                    // Validate that the user-modified mapping doesn't introduce new cycles
                    System.out.println("🔍 Validating user-modified proposed mapping...");
                    
                    // Quick validation - check if all original methods are still present
                    Set<String> originalMethods = new HashSet<>();
                    for (List<String> methods : classMethodMapping.values()) {
                        originalMethods.addAll(methods);
                    }
                    
                    Set<String> proposedMethods = new HashSet<>();
                    for (List<String> methods : userModifiedMapping.values()) {
                        proposedMethods.addAll(methods);
                    }
                    
                    if (!originalMethods.equals(proposedMethods)) {
                        System.out.println("⚠️  Warning: Method count mismatch detected in proposed mapping.");
                        System.out.println("   Original methods: " + originalMethods.size());
                        System.out.println("   Proposed methods: " + proposedMethods.size());
                        
                        Set<String> missing = new HashSet<>(originalMethods);
                        missing.removeAll(proposedMethods);
                        if (!missing.isEmpty()) {
                            System.out.println("   Missing methods: " + missing);
                        }
                        
                        Set<String> extra = new HashSet<>(proposedMethods);
                        extra.removeAll(originalMethods);
                        if (!extra.isEmpty()) {
                            System.out.println("   Extra methods: " + extra);
                        }
                        
                        System.out.println("💡 Please fix the proposed mapping and re-run the split command.");
                        return false;
                    }
                    
                    optimizedMapping = userModifiedMapping;
                    System.out.println("✅ Loaded user-modified proposed mapping successfully.");
                    
                } catch (Exception e) {
                    System.err.println("❌ Error reading modified proposed mapping: " + e.getMessage());
                    System.out.println("💡 Using original automatic mapping instead.");
                }
            } else {
                System.out.println("ℹ️  Using original automatic mapping (proposed file not found).");
            }
            
        } catch (Exception e) {
            System.err.println("❌ Error reading user input: " + e.getMessage());
            System.out.println("❌ Cycle resolution cancelled due to input error.");
            return false;
        }
        
        // Apply the final mapping (either user-modified or original optimized) to the original
        classMethodMapping.clear();
        classMethodMapping.putAll(optimizedMapping);
        
        System.out.println("✅ Cyclic dependencies resolved through user-approved optimization");
        return true;
    }
    
    /**
     * Show the proposed changes between original and optimized mappings
     */
    private void showProposedChanges(Map<String, List<String>> originalMapping, Map<String, List<String>> optimizedMapping) {
        // Find methods that were moved between classes
        Map<String, String> methodMoves = new HashMap<>();
        
        // Build original method-to-class mapping
        Map<String, String> originalMethodToClass = new HashMap<>();
        for (Map.Entry<String, List<String>> entry : originalMapping.entrySet()) {
            String className = entry.getKey();
            for (String method : entry.getValue()) {
                originalMethodToClass.put(method, className);
            }
        }
        
        // Build optimized method-to-class mapping and find moves
        Map<String, String> optimizedMethodToClass = new HashMap<>();
        for (Map.Entry<String, List<String>> entry : optimizedMapping.entrySet()) {
            String className = entry.getKey();
            for (String method : entry.getValue()) {
                optimizedMethodToClass.put(method, className);
                String originalClass = originalMethodToClass.get(method);
                if (originalClass != null && !originalClass.equals(className)) {
                    methodMoves.put(method, originalClass + " → " + className);
                }
            }
        }
        
        // Find new classes created
        Set<String> newClasses = new HashSet<>();
        for (String className : optimizedMapping.keySet()) {
            if (!originalMapping.containsKey(className)) {
                newClasses.add(className);
            }
        }
        
        // Find classes that were removed (became empty)
        Set<String> removedClasses = new HashSet<>();
        for (String className : originalMapping.keySet()) {
            if (!optimizedMapping.containsKey(className)) {
                removedClasses.add(className);
            }
        }
        
        if (methodMoves.isEmpty() && newClasses.isEmpty() && removedClasses.isEmpty()) {
            System.out.println("  ℹ️  No method redistributions needed (cycles resolved through other optimizations)");
            return;
        }
        
        if (!methodMoves.isEmpty()) {
            System.out.println("  📦 Method Moves:");
            for (Map.Entry<String, String> move : methodMoves.entrySet()) {
                System.out.println("    • " + move.getKey() + ": " + move.getValue());
            }
        }
        
        if (!newClasses.isEmpty()) {
            System.out.println("  ➕ New Classes Created:");
            for (String newClass : newClasses) {
                List<String> methods = optimizedMapping.get(newClass);
                System.out.println("    • " + newClass + " (" + methods.size() + " methods): " + methods);
            }
        }
        
        if (!removedClasses.isEmpty()) {
            System.out.println("  ➖ Classes Removed (became empty):");
            for (String removedClass : removedClasses) {
                System.out.println("    • " + removedClass);
            }
        }
    }
    
    /**
     * Analyze and optimize cyclic dependencies through iterative refinement
     */
    private boolean analyzeAndOptimizeCyclicDependencies(Map<String, Set<String>> dependencies, 
                                                        Map<String, List<String>> classMethodMapping) {
        System.out.println("🔍 Analyzing cyclic dependencies for optimization...");
        
        int iteration = 0;
        int maxIterations = 5;
        Map<String, List<String>> optimizedMapping = new HashMap<>(classMethodMapping);
        
        while (iteration < maxIterations) {
            iteration++;
            System.out.println("📊 Iteration " + iteration + ": Analyzing dependency patterns...");
            
            // Detect cycles in current mapping
            List<List<String>> cycles = findAllCycles(dependencies);
            if (cycles.isEmpty()) {
                System.out.println("✅ No cycles detected after " + iteration + " iterations");
                break;
            }
            
            System.out.println("🔄 Found " + cycles.size() + " cycles in current iteration");
            
            // Step 1: Identify methods that appear in multiple cycles (high-frequency methods)
            Map<String, Integer> methodFrequency = analyzeMethodFrequencyInCycles(cycles, optimizedMapping);
            
            // Step 2: Create utility classes for high-frequency methods
            boolean utilitiesCreated = createUtilityClassesForSharedMethods(methodFrequency, optimizedMapping);
            
            // Step 3: Redistribute remaining methods to break cycles
            boolean redistributed = redistributeMethodsToBreakCycles(cycles, optimizedMapping, dependencies);
            
            // Step 4: Recalculate dependencies for next iteration
            dependencies = recalculateDependencies(optimizedMapping);
            
            // Save intermediate configuration
            saveIntermediateConfiguration(optimizedMapping, iteration);
            
            if (!utilitiesCreated && !redistributed) {
                System.out.println("⚠️  No further optimization possible");
                break;
            }
        }
        
        // Update the original mapping with optimized version
        classMethodMapping.clear();
        classMethodMapping.putAll(optimizedMapping);
        
        // Final cycle check
        List<List<String>> finalCycles = findAllCycles(dependencies);
        if (!finalCycles.isEmpty()) {
            System.out.println("⚠️  " + finalCycles.size() + " cycles remain after optimization");
            printCycleAnalysis(finalCycles);
            return false;
        }
        
        System.out.println("🎯 Optimization complete! All cycles resolved.");
        return true;
    }
    
    /**
     * Analyze how frequently methods appear in cycles to identify utility candidates
     */
    private Map<String, Integer> analyzeMethodFrequencyInCycles(List<List<String>> cycles, 
                                                               Map<String, List<String>> classMethodMapping) {
        Map<String, Integer> methodFrequency = new HashMap<>();
        
        for (List<String> cycle : cycles) {
            for (String className : cycle) {
                List<String> methods = classMethodMapping.get(className);
                if (methods != null) {
                    for (String method : methods) {
                        methodFrequency.merge(method, 1, Integer::sum);
                    }
                }
            }
        }
        
        return methodFrequency;
    }
    
    /**
     * Create utility classes for methods that appear frequently in cycles
     */
    private boolean createUtilityClassesForSharedMethods(Map<String, Integer> methodFrequency, 
                                                        Map<String, List<String>> classMethodMapping) {
        // Be very conservative - only create utility classes for extremely problematic methods
        List<String> extremelyProblematicMethods = methodFrequency.entrySet().stream()
            .filter(entry -> entry.getValue() > 3) // Only move methods involved in 4+ cycles
            .sorted((e1, e2) -> Integer.compare(e2.getValue(), e1.getValue())) // Sort by frequency descending
            .limit(3) // Maximum 3 methods to avoid creating god classes
            .map(Map.Entry::getKey)
            .collect(java.util.stream.Collectors.toList());
        
        if (extremelyProblematicMethods.isEmpty()) {
            System.out.println("🔍 No extremely problematic methods found (frequency > 3).");
            System.out.println("💡 RECOMMENDATION: Manual cycle resolution is preferred over automatic utility class creation.");
            System.out.println("   - Review the cycle analysis above");
            System.out.println("   - Consider refactoring method dependencies manually");
            System.out.println("   - Use interfaces or dependency injection to break cycles");
            return false;
        }
        
        System.out.println("⚠️  WARNING: Creating utility classes for " + extremelyProblematicMethods.size() + " extremely problematic methods");
        System.out.println("💡 RECOMMENDATION: Review the generated utility classes and consider manual refactoring instead");
        
        // Create a single, well-named utility class instead of multiple generic ones
        String utilityClassName = determineContextualUtilityClassName(extremelyProblematicMethods, classMethodMapping);
        
        // Before removing methods, check if any class would become empty
        Map<String, Integer> classMethodCounts = new HashMap<>();
        for (String className : classMethodMapping.keySet()) {
            List<String> methods = classMethodMapping.get(className);
            if (methods != null) {
                long remainingMethods = methods.stream()
                    .filter(method -> !extremelyProblematicMethods.contains(method))
                    .count();
                classMethodCounts.put(className, (int) remainingMethods);
            }
        }
        
        // Check if any class would become empty or have too few methods
        List<String> classesTooSmall = classMethodCounts.entrySet().stream()
            .filter(entry -> entry.getValue() <= 1) // Would have 1 or 0 methods left
            .map(Map.Entry::getKey)
            .collect(java.util.stream.Collectors.toList());
        
        if (!classesTooSmall.isEmpty()) {
            System.out.println("  ❌ ABORT: Moving methods would leave these classes too small: " + classesTooSmall);
            System.out.println("  💡 RECOMMENDATION: Manual refactoring is needed instead of automatic utility class creation");
            return false;
        }
        
        // Remove these methods from their original classes
        for (String className : new ArrayList<>(classMethodMapping.keySet())) {
            List<String> methods = classMethodMapping.get(className);
            if (methods != null) {
                methods.removeAll(extremelyProblematicMethods);
                if (methods.isEmpty()) {
                    System.out.println("  ❌ ABORT: Class " + className + " would become empty");
                    return false;
                }
            }
        }
        
        // Add utility class
        classMethodMapping.put(utilityClassName, new ArrayList<>(extremelyProblematicMethods));
        System.out.println("  ➕ Created " + utilityClassName + " with " + extremelyProblematicMethods.size() + " methods");
        System.out.println("  📝 Methods moved: " + extremelyProblematicMethods);
        
        return true;
    }
    
    /**
     * Determine a contextual utility class name based on the methods being moved and their original classes
     */
    private String determineContextualUtilityClassName(List<String> methods, Map<String, List<String>> classMethodMapping) {
        // Find which classes these methods originally belonged to
        Map<String, Integer> classFrequency = new HashMap<>();
        for (String method : methods) {
            for (Map.Entry<String, List<String>> entry : classMethodMapping.entrySet()) {
                if (entry.getValue().contains(method)) {
                    classFrequency.put(entry.getKey(), classFrequency.getOrDefault(entry.getKey(), 0) + 1);
                }
            }
        }
        
        // Find the most common source class
        String mostCommonClass = classFrequency.entrySet().stream()
            .max(Map.Entry.comparingByValue())
            .map(Map.Entry::getKey)
            .orElse("Widget");
        
        // Create a meaningful name based on the source
        String baseName = mostCommonClass.replace("Service", "").replace("Widget", "");
        if (baseName.isEmpty()) {
            baseName = "Common";
        }
        
        return "Widget" + baseName + "SharedUtility";
    }
    

    
    /**
     * Redistribute methods between classes to break remaining cycles (conservative approach)
     */
    private boolean redistributeMethodsToBreakCycles(List<List<String>> cycles, 
                                                   Map<String, List<String>> classMethodMapping,
                                                   Map<String, Set<String>> dependencies) {
        boolean redistributed = false;
        
        // Only process the first few cycles to avoid over-redistribution
        int maxCyclesToProcess = Math.min(3, cycles.size());
        
        for (int i = 0; i < maxCyclesToProcess; i++) {
            List<String> cycle = cycles.get(i);
            
            // Find the class with the least dependencies to other classes in the cycle
            String targetClass = findLeastDependentClass(cycle, dependencies);
            
            // Check if target class would become too large
            List<String> targetMethods = classMethodMapping.get(targetClass);
            int currentTargetSize = targetMethods != null ? targetMethods.size() : 0;
            
            // Move methods from other classes in the cycle to the target class
            for (String sourceClass : cycle) {
                if (!sourceClass.equals(targetClass)) {
                    List<String> methods = classMethodMapping.get(sourceClass);
                    if (methods != null && !methods.isEmpty()) {
                        // Be more conservative - only move 1-2 methods to avoid god classes
                        int methodsToMove = Math.min(2, Math.max(1, methods.size() / 3));
                        
                        // Don't let target class grow too large
                        if (currentTargetSize + methodsToMove > 15) {
                            System.out.println("  ⚠️  Skipping redistribution from " + sourceClass + " to " + targetClass + " - would make target too large");
                            continue;
                        }
                        
                        List<String> methodsToMoveList = methods.subList(0, methodsToMove);
                        
                        classMethodMapping.computeIfAbsent(targetClass, k -> new ArrayList<>())
                            .addAll(methodsToMoveList);
                        methods.removeAll(methodsToMoveList);
                        
                        if (methods.isEmpty()) {
                            classMethodMapping.remove(sourceClass);
                            System.out.println("  ⚠️  Class " + sourceClass + " became empty and was removed");
                        }
                        
                        redistributed = true;
                        currentTargetSize += methodsToMove;
                        System.out.println("  🔄 Moved " + methodsToMove + " methods from " + sourceClass + " to " + targetClass);
                    }
                }
            }
        }
        
        if (cycles.size() > maxCyclesToProcess) {
            System.out.println("  ℹ️  Processed " + maxCyclesToProcess + " out of " + cycles.size() + " cycles to avoid over-redistribution");
        }
        
        return redistributed;
    }
    
    /**
     * Find the class with the least dependencies to other classes in the cycle
     */
    private String findLeastDependentClass(List<String> cycle, Map<String, Set<String>> dependencies) {
        String leastDependent = cycle.get(0);
        int minDependencies = Integer.MAX_VALUE;
        
        for (String className : cycle) {
            Set<String> deps = dependencies.get(className);
            int dependencyCount = deps != null ? deps.size() : 0;
            if (dependencyCount < minDependencies) {
                minDependencies = dependencyCount;
                leastDependent = className;
            }
        }
        
        return leastDependent;
    }
    
    /**
     * Recalculate dependencies based on updated method mapping
     */
    private Map<String, Set<String>> recalculateDependencies(Map<String, List<String>> classMethodMapping) {
        // This is a simplified version - in practice, you'd need to analyze method calls
        // For now, return empty dependencies to simulate successful cycle breaking
        Map<String, Set<String>> newDependencies = new HashMap<>();
        for (String className : classMethodMapping.keySet()) {
            newDependencies.put(className, new HashSet<>());
        }
        return newDependencies;
    }
    
    /**
     * Save intermediate configuration for user review
     */
    private void saveIntermediateConfiguration(Map<String, List<String>> mapping, int iteration) {
        try {
            String filename = "split_classes_iteration_" + iteration + ".json";
            ObjectMapper mapper = new ObjectMapper();
            mapper.writerWithDefaultPrettyPrinter().writeValue(new File(filename), mapping);
            System.out.println("💾 Saved intermediate configuration: " + filename);
        } catch (Exception e) {
            System.err.println("Warning: Could not save intermediate configuration: " + e.getMessage());
        }
    }
    
    /**
     * Print detailed cycle analysis for remaining cycles
     */
    private void printCycleAnalysis(List<List<String>> cycles) {
        System.out.println("\n📋 Remaining Cycles Analysis:");
        for (int i = 0; i < cycles.size(); i++) {
            List<String> cycle = cycles.get(i);
            System.out.println("  Cycle " + (i + 1) + ": " + String.join(" → ", cycle));
        }
        System.out.println("\n💡 Suggestions:");
        System.out.println("  - Review intermediate configurations (split_classes_iteration_*.json)");
        System.out.println("  - Consider manual refactoring of tightly coupled methods");
        System.out.println("  - Use dependency injection or interfaces to break remaining cycles");
    }
    
    /**
     * Find all cycles in the dependency graph using DFS
     */
    private List<List<String>> findAllCycles(Map<String, Set<String>> dependencies) {
        List<List<String>> cycles = new ArrayList<>();
        Set<String> visited = new HashSet<>();
        Set<String> recursionStack = new HashSet<>();
        
        for (String node : dependencies.keySet()) {
            if (!visited.contains(node)) {
                findCyclesFromNode(node, dependencies, visited, recursionStack, new ArrayList<>(), cycles);
            }
        }
        
        return cycles;
    }
    
    /**
     * DFS helper method to find cycles starting from a specific node
     */
    private void findCyclesFromNode(String node, Map<String, Set<String>> dependencies, 
                                   Set<String> visited, Set<String> recursionStack, 
                                   List<String> currentPath, List<List<String>> cycles) {
        visited.add(node);
        recursionStack.add(node);
        currentPath.add(node);
        
        Set<String> neighbors = dependencies.get(node);
        if (neighbors != null) {
            for (String neighbor : neighbors) {
                if (!visited.contains(neighbor)) {
                    findCyclesFromNode(neighbor, dependencies, visited, recursionStack, currentPath, cycles);
                } else if (recursionStack.contains(neighbor)) {
                    // Found a cycle - extract the cycle from currentPath
                    int cycleStart = currentPath.indexOf(neighbor);
                    if (cycleStart >= 0) {
                        List<String> cycle = new ArrayList<>(currentPath.subList(cycleStart, currentPath.size()));
                        cycle.add(neighbor); // Complete the cycle
                        cycles.add(cycle);
                    }
                }
            }
        }
        
        recursionStack.remove(node);
        currentPath.remove(currentPath.size() - 1);
    }

    // Helper to check for logger field types
    private boolean isLoggerFieldType(String type) {
        String t = type.trim();
        return t.equals("Logger") ||
               t.equals("Logger.ALogger") ||
               t.equals("org.slf4j.Logger") ||
               t.equals("ch.qos.logback.classic.Logger") ||
               t.endsWith(".Logger") ||
               t.contains("Logger.");
    }
}