from datetime import timedelta, datetime

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.http.response import Http404

from .inbus import inbus
import django.contrib.auth.models
import re
from functools import lru_cache
from ipware import get_client_ip
from typing import NewType

IPAddressString = NewType("IPAddressString", str)


@lru_cache()
def is_teacher(user):
    return user.groups.filter(name="teachers").exists()


def points_to_color(points, max_points):
    ratio = max(0, min(1, points / max_points))
    green = int(ratio * 200)
    red = int((1 - ratio) * 255)
    return f"#{red:02X}{green:02X}00"


def parse_time_interval(text):
    patterns = [
        r"(?P<days>\d+)\s*(d|day|days)",
        r"(?P<minutes>\d+)\s*(m|min|minute|minutes)",
        r"(?P<hours>\d+)\s*(h|hour|hours)",
        r"(?P<weeks>\d+)\s*(w|week|weeks)",
    ]

    parsed = {}
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            parsed = {**parsed, **{k: int(v) for k, v in match.groupdict().items()}}
    return timedelta(**parsed)


def inbus_search_user(login: str) -> inbus.dto.PersonSimple | None:
    return inbus.search_user(login)


def user_from_inbus_person(person: inbus.dto.PersonSimple) -> django.contrib.auth.models.User:
    """
    Returns a Django user from provided person info.

    NOTE: `username` is not set and has to be provided later on.
    """
    user = django.contrib.auth.models.User(
        first_name=person.first_name, last_name=person.second_name, email=person.email
    )

    return user


def user_from_login(login: str) -> django.contrib.auth.models.User | None:
    """
    A shotcut to calling `inbus_search_user` and `user_from_inbus_person`.
    No need to further set anything.
    """
    person = inbus_search_user(login)
    if not person:
        return None
    user = user_from_inbus_person(person)
    user.username = login.upper()
    user.save()

    return user


def get_client_ip_address(request: HttpRequest) -> IPAddressString | None:
    """
    Returns client IP address from HttpRequest instance.
    Returns None if no client IP address is available.
    """
    client_ip, is_routable = get_client_ip(request)

    if client_ip is None:
        return None
    else:
        return IPAddressString(client_ip)


def prohibit_during_test(function):
    """
    Decorator that restricts access to a page if the student has any ongoing exams.

    The decorated function must accept the following parameters:
        - request
        - assignment_id

    Access is granted only for tasks whose hard deadline ends before the exam starts.
    """

    def wrapper(*args, **kwargs):
        from .models import AssignedTask
        from .task import get_active_exams_at

        request = args[0]

        if is_teacher(request.user):
            return function(*args, **kwargs)

        active_exams = get_active_exams_at(request.user, datetime.now(), timedelta(0))

        if not active_exams:
            return function(*args, **kwargs)

        assignment_id = kwargs.get("assignment_id")

        try:
            assignment = AssignedTask.objects.get(pk=assignment_id)
        except AssignedTask.DoesNotExist:
            raise Http404(f"AssignedTask with id {assignment_id} not found")

        # if task is any of running exams allow it
        is_current_exam = any(map(lambda e: e.pk == assignment.pk, active_exams))

        if is_current_exam:
            return function(*args, **kwargs)

        if assignment.has_hard_deadline() and assignment.deadline is not None:
            # check if the deadline has expired before the start of all exams
            if all(map(lambda e: assignment.deadline < e.assigned, active_exams)):
                return function(*args, **kwargs)

        raise PermissionDenied("Access to this task is prohibited during exam")

    return wrapper
