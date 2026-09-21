"""GitHub API client for ReviewPilot.

Handles all GitHub interactions: fetching PR diffs, posting review
comments, and managing comment lifecycle. Uses PyGithub for the
REST API with proper error handling for rate limits and 422 errors.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from github import Auth, Github, GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

logger = logging.getLogger(__name__)


class GitHubClient:
    """Wrapper around GitHub REST API for PR review operations."""

    def __init__(self, token: Optional[str] = None) -> None:
        """Initialize GitHub client.

        Args:
            token: GitHub token. If None, reads from GITHUB_TOKEN env var.
        """
        self._token = token or os.environ.get("GITHUB_TOKEN", "")
        if not self._token:
            raise GitHubClientError("No GitHub token provided. Set GITHUB_TOKEN env var.")

        auth = Auth.Token(self._token)
        self._github = Github(auth=auth)

    def get_pr(self, repo_full_name: str, pr_number: int) -> PullRequest:
        """Get a Pull Request object.

        Args:
            repo_full_name: Repository in 'owner/repo' format
            pr_number: PR number

        Returns:
            PyGithub PullRequest object
        """
        repo = self._github.get_repo(repo_full_name)
        return repo.get_pull(pr_number)

    def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Fetch the unified diff for a Pull Request.

        Uses the GitHub API media type 'application/vnd.github.v3.diff'
        to get the raw unified diff.

        Args:
            repo_full_name: Repository in 'owner/repo' format
            pr_number: PR number

        Returns:
            Raw unified diff string
        """
        import httpx

        url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
        response.raise_for_status()
        return response.text

    def get_pr_files_content(
        self,
        repo_full_name: str,
        pr_number: int,
        file_paths: list[str],
    ) -> dict[str, str]:
        """Fetch the content of files at the PR's HEAD commit.

        Args:
            repo_full_name: Repository in 'owner/repo' format
            pr_number: PR number
            file_paths: List of file paths to fetch

        Returns:
            Dict mapping file_path -> file content
        """
        repo = self._github.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        head_sha = pr.head.sha

        contents: dict[str, str] = {}
        for path in file_paths:
            try:
                file_content = repo.get_contents(path, ref=head_sha)
                if hasattr(file_content, "decoded_content"):
                    contents[path] = file_content.decoded_content.decode("utf-8")
            except GithubException as e:
                logger.warning("Could not fetch %s at %s: %s", path, head_sha, e)

        return contents

    def post_review(
        self,
        repo_full_name: str,
        pr_number: int,
        comments: list[dict],
        body: str = "",
    ) -> None:
        """Post a review with inline comments on a PR.

        Uses the GitHub Pull Request Review API to batch-submit
        all comments in a single API call.

        Args:
            repo_full_name: Repository in 'owner/repo' format
            pr_number: PR number
            comments: List of comment dicts with keys:
                - path: file path
                - line: end line number
                - side: 'RIGHT' or 'LEFT'
                - body: comment markdown
                - start_line: (optional) start line for multi-line
            body: Overall review body text

        Raises:
            GitHubClientError: If the API call fails
        """
        repo = self._github.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        head_sha = pr.head.sha

        # GitHub limits to ~60 comments per review
        batch_size = 25
        for i in range(0, len(comments), batch_size):
            batch = comments[i : i + batch_size]
            review_body = body if i == 0 else ""  # Only include body in first batch

            try:
                pr.create_review(
                    commit=repo.get_commit(head_sha),
                    body=review_body,
                    event="COMMENT",
                    comments=batch,
                )
                logger.info(
                    "Posted review batch %d/%d (%d comments)",
                    i // batch_size + 1,
                    (len(comments) + batch_size - 1) // batch_size,
                    len(batch),
                )
            except GithubException as e:
                if e.status == 422:
                    logger.error(
                        "422 Unprocessable Entity — comment lines may be outside diff bounds. "
                        "Attempting individual comment fallback. Error: %s",
                        e.data,
                    )
                    self._post_comments_individually(pr, repo, head_sha, batch)
                else:
                    raise GitHubClientError(f"Failed to post review: {e}") from e

    def post_summary_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
    ) -> None:
        """Post a top-level summary comment on the PR.

        Args:
            repo_full_name: Repository in 'owner/repo' format
            pr_number: PR number
            body: Comment body in markdown
        """
        repo = self._github.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        pr.create_issue_comment(body)

    def _post_comments_individually(
        self,
        pr: PullRequest,
        repo: Repository,
        head_sha: str,
        comments: list[dict],
    ) -> None:
        """Fallback: post comments one by one, skipping any that fail.

        Used when a batch review fails with 422 — some comments may have
        line numbers outside the diff bounds.
        """
        commit = repo.get_commit(head_sha)
        for comment in comments:
            try:
                pr.create_review(
                    commit=commit,
                    body="",
                    event="COMMENT",
                    comments=[comment],
                )
            except GithubException as e:
                logger.warning(
                    "Skipping comment on %s:%s — %s",
                    comment.get("path", "?"),
                    comment.get("line", "?"),
                    e.data if hasattr(e, "data") else str(e),
                )

    def close(self) -> None:
        """Close the GitHub client connection."""
        self._github.close()


class GitHubClientError(Exception):
    """Raised when GitHub API operations fail."""
    pass
