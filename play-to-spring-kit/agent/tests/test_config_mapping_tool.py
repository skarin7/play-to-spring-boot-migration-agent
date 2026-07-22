"""tools/config_mapping.py tests: HOCON flattening, properties parsing, seed diff, append."""

from agent.tools.config_mapping import (
    SEED_KEY_MAP,
    append_properties,
    diff_config_keys,
    flatten_play_conf,
    read_properties_keys,
)

CONF_TXT = """\
mongodb {
  uri = "mongodb://localhost:27017/mydb"
}

spring {
  datasource {
    url = "jdbc:postgresql://localhost/db"
  }
}

app {
  secret = "changeme"
}
"""


def test_flatten_play_conf_flattens_nested_objects(tmp_path):
    conf_path = tmp_path / "application.conf"
    conf_path.write_text(CONF_TXT, encoding="utf-8")
    flat = flatten_play_conf(conf_path)
    assert flat["mongodb.uri"] == "mongodb://localhost:27017/mydb"
    assert flat["spring.datasource.url"] == "jdbc:postgresql://localhost/db"
    assert flat["app.secret"] == "changeme"


def test_flatten_play_conf_missing_file_returns_empty(tmp_path):
    assert flatten_play_conf(tmp_path / "does-not-exist.conf") == {}


def test_read_properties_keys_skips_comments_and_blanks(tmp_path):
    props_path = tmp_path / "application.properties"
    props_path.write_text(
        "# a comment\n\nspring.data.mongodb.uri=mongodb://existing\n\nserver.port=8080\n",
        encoding="utf-8",
    )
    keys = read_properties_keys(props_path)
    assert keys == {"spring.data.mongodb.uri", "server.port"}


def test_read_properties_keys_missing_file_returns_empty_set(tmp_path):
    assert read_properties_keys(tmp_path / "application.properties") == set()


def test_diff_config_keys_seed_hit_already_idiomatic_and_unknown(tmp_path):
    conf_path = tmp_path / "application.conf"
    conf_path.write_text(CONF_TXT, encoding="utf-8")
    flattened = flatten_play_conf(conf_path)

    seed_mapped, leftover = diff_config_keys(flattened, existing_keys=set())

    # Seed-table hit -> canonical Spring key, ready to append.
    assert seed_mapped == {"spring.data.mongodb.uri": "mongodb://localhost:27017/mydb"}
    # Already Spring-idiomatic key ("spring.datasource.url") is skipped entirely.
    # Genuinely unknown key lands in leftover.
    assert leftover == {"app.secret": "changeme"}
    assert "spring.datasource.url" not in leftover
    assert "spring.datasource.url" not in seed_mapped.values()


def test_diff_config_keys_does_not_readd_already_present_seed_key(tmp_path):
    flattened = {"mongodb.uri": "mongodb://localhost:27017/mydb"}
    existing_keys = {"spring.data.mongodb.uri"}

    seed_mapped, leftover = diff_config_keys(flattened, existing_keys)

    assert seed_mapped == {}
    assert leftover == {}  # already resolved -- not leftover either


def test_seed_key_map_has_expected_entries():
    assert SEED_KEY_MAP["mongodb.uri"] == "spring.data.mongodb.uri"
    assert SEED_KEY_MAP["mongo.uri"] == "spring.data.mongodb.uri"
    assert SEED_KEY_MAP["kafka.bootstrap.servers"] == "spring.kafka.bootstrap-servers"


def test_append_properties_creates_file_if_missing(tmp_path):
    props_path = tmp_path / "resources" / "application.properties"
    append_properties(props_path, {"spring.data.mongodb.uri": "mongodb://x"})
    assert props_path.is_file()
    assert "spring.data.mongodb.uri=mongodb://x" in props_path.read_text(encoding="utf-8")


def test_append_properties_appends_to_existing_file(tmp_path):
    props_path = tmp_path / "application.properties"
    props_path.write_text("server.port=8080\n", encoding="utf-8")
    append_properties(props_path, {"spring.data.mongodb.uri": "mongodb://x"})
    text = props_path.read_text(encoding="utf-8")
    assert "server.port=8080" in text
    assert "spring.data.mongodb.uri=mongodb://x" in text


def test_append_properties_noop_on_empty_entries(tmp_path):
    props_path = tmp_path / "application.properties"
    append_properties(props_path, {})
    assert not props_path.exists()
