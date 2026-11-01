from __future__ import annotations

from nicegui import run, ui

from auth import login_session, verify_credentials


def render_login_page(
    *, session_expired: bool = False, redirect_to: str = "/",
    title: str = "Document Realism Study", signed_in_as: str | None = None,
) -> None:
    """signed_in_as: the account this browser is already signed in with. The form is still shown, so
    another person can sign in on the same computer (that signs the previous account out)."""
    with ui.column().classes("absolute-center login-wrap items-center gap-5"):
        with ui.column().classes("items-center gap-0"):
            ui.label(title).classes("title")
            ui.label("Sign in with the account you were given.").classes("muted")
        with ui.card().classes("card w-full gap-3"):
            if signed_in_as:
                with ui.column().classes("notice w-full gap-1"):
                    ui.label(f"This browser is signed in as {signed_in_as}.")
                    ui.button(f"Continue as {signed_in_as}", on_click=lambda: ui.navigate.to(redirect_to)
                              ).props("outline no-caps dense")
                    ui.label(f"Or sign in below with another account ({signed_in_as} is signed out; "
                             "their answers stay saved).").classes("text-sm")
            if session_expired:
                ui.label("Your session expired after 30 minutes without activity. "
                         "Your answers so far are saved.").classes("notice w-full")
            error = ui.label("").classes("notice w-full")
            error.set_visibility(False)
            username = ui.input("Username").props("outlined autofocus").classes("w-full")
            password = ui.input("Password", password=True, password_toggle_button=True).props(
                "outlined").classes("w-full")

            async def do_login() -> None:
                name, secret = (username.value or "").strip(), password.value or ""
                if not name or not secret or not await run.io_bound(
                        verify_credentials, name, secret):
                    error.set_text("Invalid username or password.")
                    error.set_visibility(True)
                    password.value = ""
                    return
                login_session(name)
                ui.navigate.to(redirect_to)

            password.on("keydown.enter", do_login)
            username.on("keydown.enter", do_login)
            ui.button("Sign in", on_click=do_login).props("unelevated no-caps").classes(
                "w-full big-btn")
