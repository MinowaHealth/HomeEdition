from TestData.three_month_seed.__main__ import build_parser


def test_default_no_flags():
    args = build_parser().parse_args([])
    assert args.reset is False
    assert args.verify_only is False
    assert args.no_embeddings is False
    assert args.persona is None


def test_reset_flag():
    args = build_parser().parse_args(["--reset"])
    assert args.reset is True


def test_verify_only_flag():
    args = build_parser().parse_args(["--verify-only"])
    assert args.verify_only is True


def test_persona_flag():
    args = build_parser().parse_args(["--persona=rodrigo"])
    assert args.persona == "rodrigo"


def test_no_embeddings_flag():
    args = build_parser().parse_args(["--no-embeddings"])
    args2 = build_parser().parse_args([])
    assert args.no_embeddings is True
    assert args2.no_embeddings is False
