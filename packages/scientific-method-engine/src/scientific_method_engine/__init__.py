"""Bounded instruction-derived x86 evidence reports."""

# The reader passes this number with every prepared config it pipes to the engine. A reader and
# engine that disagree on it refuse to run instead of exchanging relocation data in another shape.
PREPARED_PROTOCOL = 3
