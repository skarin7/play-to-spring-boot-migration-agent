import pytest

from agent.tools.fs import FsJail


@pytest.fixture
def repos(tmp_path):
    spring = tmp_path / "spring"
    play = tmp_path / "play"
    (spring / "src" / "main" / "java").mkdir(parents=True)
    (play / "app").mkdir(parents=True)
    (spring / "pom.xml").write_text("<project/>")
    (spring / "src" / "main" / "java" / "A.java").write_text("class A {}")
    (play / "app" / "B.java").write_text("class B {}")
    (tmp_path / "secret.txt").write_text("nope")
    return spring, play


def get_tool(jail: FsJail, name: str):
    return next(t for t in jail.build_tools() if t.name == name)


def test_read_spring_and_play(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    read = get_tool(jail, "read_file")
    assert "class A" in read.invoke({"path": "src/main/java/A.java"})
    assert "class B" in read.invoke({"path": str(play / "app" / "B.java")})


def test_read_outside_roots_blocked(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    read = get_tool(jail, "read_file")
    with pytest.raises(PermissionError):
        read.invoke({"path": str(spring.parent / "secret.txt")})


def test_write_jail(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    write = get_tool(jail, "write_file")
    # allowed: src/** and pom.xml
    write.invoke({"path": "src/main/java/New.java", "content": "class New {}"})
    write.invoke({"path": "pom.xml", "content": "<project></project>"})
    # blocked: play repo, spring repo root, outside
    with pytest.raises(PermissionError):
        write.invoke({"path": str(play / "app" / "B.java"), "content": "x"})
    with pytest.raises(PermissionError):
        write.invoke({"path": "README.md", "content": "x"})
    assert len(jail.edited_files) == 2


def test_write_traversal_blocked(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    write = get_tool(jail, "write_file")
    with pytest.raises(PermissionError):
        write.invoke({"path": "src/../../escape.java", "content": "x"})


def test_str_replace_uniqueness(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    replace = get_tool(jail, "str_replace")
    target = spring / "src" / "main" / "java" / "A.java"
    target.write_text("aa bb aa")
    assert "error" in replace.invoke({"path": str(target), "old": "aa", "new": "cc"})
    assert "edited" in replace.invoke({"path": str(target), "old": "bb", "new": "cc"})
    assert target.read_text() == "aa cc aa"
