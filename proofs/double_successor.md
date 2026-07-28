# Double successor

## Statement

For every natural number `n`, adding `n + 1` to itself gives two more than
adding `n` to itself:

`(n + 1) + (n + 1) = (n + n) + 2`.

## Human proof

Expand each occurrence of “plus one” as a successor. Adding a successor on the
right produces the successor of the sum, so doing this twice produces two
successors. The right-hand side also adds two successors to `n + n`. Therefore
both sides are equal.

The formal result should quantify explicitly over `n : Nat` and use no
unproved assumptions.
