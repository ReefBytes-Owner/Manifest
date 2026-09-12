#!/usr/bin/env bats
# Valid fixture: a single passing assertion.

@test "addition works" {
  result="$((2 + 2))"
  [ "$result" -eq 4 ]
}
