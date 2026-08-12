package com.phenom.devtoolkit;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.github.javaparser.JavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.MethodDeclaration;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileWriter;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class MethodIntrospector {
    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.out.println("Usage: java -cp <jar> com.phenom.refactor.MethodIntrospector <input-file>");
            return;
        }
        String inputFile = args[0];
        File file = new File(inputFile);
        String className = file.getName().replaceFirst("\\.java$", "");
        JavaParser parser = new JavaParser();
        CompilationUnit cu = parser.parse(new FileInputStream(file)).getResult().orElseThrow(() -> new RuntimeException("Failed to parse Java file"));
        List<MethodDeclaration> methods = cu.findAll(MethodDeclaration.class);
        Map<String, ObjectNode> methodInfo = new HashMap<>();
        ObjectMapper mapper = new ObjectMapper();
        for (MethodDeclaration method : methods) {
            ObjectNode node = mapper.createObjectNode();
            int numLines = method.getEnd().isPresent() && method.getBegin().isPresent() ?
                method.getEnd().get().line - method.getBegin().get().line + 1 : 0;
            node.put("num_lines", numLines);
            methodInfo.put(method.getNameAsString(), node);
        }
        FileWriter writer = new FileWriter(className + "_methods.json");
        mapper.writerWithDefaultPrettyPrinter().writeValue(writer, methodInfo);
        System.out.println("Method info written to " + className + "_methods.json");
    }
} 