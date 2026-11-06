"""Pure-function tests for the geographic routing helper."""
import math

from app.services.agent_routing import haversine_km


def test_haversine_zero_distance():
    assert haversine_km(40.4168, -3.7038, 40.4168, -3.7038) == 0.0


def test_haversine_madrid_to_getafe_about_15km():
    # Madrid Sol → Getafe centro ≈ 13-15 km.
    d = haversine_km(40.4168, -3.7038, 40.3057, -3.7327)
    assert 10 < d < 20


def test_haversine_madrid_to_barcelona_about_500km():
    d = haversine_km(40.4168, -3.7038, 41.3851, 2.1734)
    assert 480 < d < 530


def test_haversine_symmetric():
    a = haversine_km(40.4, -3.7, 41.4, 2.1)
    b = haversine_km(41.4, 2.1, 40.4, -3.7)
    assert math.isclose(a, b, rel_tol=1e-9)
