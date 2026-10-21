Hidden ground truth. df_* columns are the supplied loan-dataset row each customer was
drawn from; stress is the latent 0..1 driver of balances/lateness; sim_* are the
simulated loan state; loan_visible=False means the loan sits at a bank that exposes no
loan API here (its instalments still appear as outgoing transfers).
Do not use as model features when the goal is to learn from API-shaped data.
