def main() -> None:
    """Verify that the uvm package can be imported successfully."""
    try:
        import uvm  # noqa: F401

        print("uvm imported successfully")  # noqa: T201
    except ImportError as e:
        print(f"Failed to import uvm: {e}")  # noqa: T201


if __name__ == "__main__":
    main()
