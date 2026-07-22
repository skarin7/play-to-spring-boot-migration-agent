package com.example;

public class Unfixable {
    public void call() {
        DoesNotExistAnywhere x = new DoesNotExistAnywhere();
        x.run();
    }
}
