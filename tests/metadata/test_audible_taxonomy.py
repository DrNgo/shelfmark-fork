from shelfmark.metadata_providers.audible import REGION_TLDS
from shelfmark.metadata_providers.audible_taxonomy import (
    CORE_TOPIC_DEFINITIONS,
    CORE_TOPIC_PATHS_BY_REGION,
    MAX_TAXONOMY_DEPTH,
    MAX_TAXONOMY_NODES,
    find_audible_topic,
    matching_core_topic_key,
    parse_audible_topic_tree,
    preferred_topic_label,
)


def test_parser_preserves_full_paths_and_skips_malformed_siblings():
    payload = {
        "categories": [
            {
                "id": "10",
                "name": "Science Fiction & Fantasy",
                "children": [
                    {"id": "11", "name": "Fantasy", "children": []},
                    {"id": "bad", "name": "Broken", "children": []},
                ],
            }
        ]
    }

    nodes = parse_audible_topic_tree(payload)

    assert nodes is not None
    fantasy = find_audible_topic(nodes, ["Science Fiction & Fantasy", "Fantasy"])
    assert fantasy is not None
    assert fantasy.category_id == "11"
    assert fantasy.path == ("Science Fiction & Fantasy", "Fantasy")


def test_repeated_leaf_names_require_complete_path():
    payload = {
        "categories": [
            {"id": "1", "name": "Romance", "children": [{"id": "2", "name": "Fantasy"}]},
            {
                "id": "3",
                "name": "Science Fiction & Fantasy",
                "children": [{"id": "4", "name": "Fantasy"}],
            },
        ]
    }

    nodes = parse_audible_topic_tree(payload)

    assert nodes is not None
    assert find_audible_topic(nodes, ["Romance", "Fantasy"]).category_id == "2"
    assert find_audible_topic(nodes, ["Science Fiction & Fantasy", "Fantasy"]).category_id == "4"


def test_path_matching_trims_segment_edges():
    nodes = parse_audible_topic_tree(
        {
            "categories": [
                {
                    "id": "1",
                    "name": " Science Fiction & Fantasy ",
                    "children": [{"id": "2", "name": " Fantasy "}],
                }
            ]
        }
    )

    assert nodes is not None
    fantasy = find_audible_topic(nodes, [" Science Fiction & Fantasy ", " Fantasy "])
    assert fantasy is not None
    assert fantasy.path == ("Science Fiction & Fantasy", "Fantasy")
    assert (
        matching_core_topic_key("us", [" Science Fiction & Fantasy ", " Fantasy "])
        == "topic_fantasy"
    )


def test_every_region_defines_every_permanent_topic():
    expected_keys = {definition.key for definition in CORE_TOPIC_DEFINITIONS}

    assert set(CORE_TOPIC_PATHS_BY_REGION) == set(REGION_TLDS)
    assert all(set(paths) == expected_keys for paths in CORE_TOPIC_PATHS_BY_REGION.values())


def test_localized_path_matches_core_key_and_builds_label():
    path = ["SF・ファンタジー", "ファンタジー"]

    assert matching_core_topic_key("jp", path) == "topic_fantasy"
    assert (
        preferred_topic_label(["SF・ファンタジー", "ファンタジー", "ダークファンタジー"])
        == "SF・ファンタジー — ダークファンタジー"
    )


def test_parser_stops_after_maximum_depth():
    child = {"id": "9", "name": "Too deep", "children": []}
    for depth in range(8, 0, -1):
        child = {"id": str(depth), "name": f"Level {depth}", "children": [child]}

    nodes = parse_audible_topic_tree({"categories": [child]})

    assert nodes is not None
    current = nodes[0]
    accepted_depth = 1
    while current.children:
        current = current.children[0]
        accepted_depth += 1
    assert accepted_depth == MAX_TAXONOMY_DEPTH


def test_parser_stops_after_maximum_node_count():
    categories = [
        {"id": str(index), "name": f"Topic {index}", "children": []}
        for index in range(MAX_TAXONOMY_NODES + 25)
    ]

    nodes = parse_audible_topic_tree({"categories": categories})

    assert nodes is not None
    assert len(nodes) == MAX_TAXONOMY_NODES
