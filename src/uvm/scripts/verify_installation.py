def main() -> None:
    try:
        import uvm  # noqa: F401

        print("uvm imported successfully")
    except ImportError as e:
        print(f"Failed to import uvm: {e}")


if __name__ == "__main__":
    main()
