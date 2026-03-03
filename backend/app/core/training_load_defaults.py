from math import exp, log1p


DEFAULT_TRAINING_LOAD_ZONE_BOUNDARIES_PCT = [56.0, 80.0, 90.0, 95.0, 102.0, 106.0]

DEFAULT_TRAINING_LOAD_ATL_DAYS = 7.0
DEFAULT_TRAINING_LOAD_CTL_DAYS = 42.0

# These parameters and the function might be specific to you (i imagine to HR zones, threshold HR or max HR). To tune them as in you r Coros account, you can solve the softplus function against 60' long activities at a certain HR.
TRAINING_LOAD_SOFTPLUS4_A = 1625.44208163
TRAINING_LOAD_SOFTPLUS4_B = 0.0317772144995
TRAINING_LOAD_SOFTPLUS4_C = 231.914410461
TRAINING_LOAD_SOFTPLUS4_D = -6.52164236211


def softplus4_training_load_per_hour(hr_bpm: float, *, max_hr_bpm: float = 196.0) -> float:
	hr = float(hr_bpm)
	max_hr = max(1.0, float(max_hr_bpm))

	if hr <= (max_hr / 2.0):
		return 0.0

	effective_hr = min(hr, max_hr)
	z = TRAINING_LOAD_SOFTPLUS4_B * (effective_hr - TRAINING_LOAD_SOFTPLUS4_C)
	softplus = log1p(exp(z))
	return (TRAINING_LOAD_SOFTPLUS4_A * softplus) + TRAINING_LOAD_SOFTPLUS4_D
