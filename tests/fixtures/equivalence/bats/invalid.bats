#!/usr/bin/env bats
# Invalid fixture: a single failing assertion.

@test "addition is wrong on purpose" {
  result="$((2 + 2))"
  [ "$result" -eq 5 ]
}
