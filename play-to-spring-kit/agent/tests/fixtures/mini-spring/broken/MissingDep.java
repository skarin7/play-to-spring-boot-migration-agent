package com.example;

import com.google.common.collect.Lists;

import java.util.List;

public class MissingDep {
    public List<String> names() {
        return Lists.newArrayList("a", "b");
    }
}
