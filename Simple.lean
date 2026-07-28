namespace Simple

-- Deliberately small declarations for the live AI-generated-check workflow.
theorem add_zero (n : Nat) : n + 0 = n := by
  induction n with
  | zero => rfl
  | succ n _ =>
      simp

theorem add_one (n : Nat) : n + 1 = Nat.succ n := by
  simp

theorem two_plus_two : 2 + 2 = 4 := by
  rfl

end Simple
