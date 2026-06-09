#!/usr/bin/env python3
"""Create Memgraph indexes and constraints."""

import sys
sys.path.insert(0, "transform_service")

import memgraph_client

if __name__ == "__main__":
    print("Creating Memgraph indexes and constraints…")
    memgraph_client.create_indexes()
    print("Done.")
