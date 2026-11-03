from jambachat.security.crypto import generate_key


def main() -> None:
    print(f"ENCRYPTION_KEYS={generate_key()}")
    print(f"INDEX_KEY={generate_key()}")


if __name__ == "__main__":
    main()
