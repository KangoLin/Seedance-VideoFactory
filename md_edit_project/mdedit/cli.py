import argparse
import json
import os
import sys
from mdedit import __version__
from mdedit.cache import Cache
from mdedit.ingest import ingest
from mdedit.analyze import analyze_all
from mdedit.plan import plan_episode
from mdedit.preview import render_preview
from mdedit.render import render_episode


def main():
    parser = argparse.ArgumentParser(
        description="manga-drama AI video editor (md-edit)"
    )
    parser.add_argument("--input", "-i", nargs="+", required=True,
                        help="Input video clip glob patterns")
    parser.add_argument("--output", "-o", default="episode.mp4",
                        help="Output video path")
    parser.add_argument("--supervisor", "-s", default=None,
                        help="Supervisor prompt file path")
    parser.add_argument("--bgm", default=None,
                        help="Background music file path")
    parser.add_argument("--work-dir", default=".md_cache",
                        help="Working directory for cache/temp files")
    parser.add_argument("--cache-ttl", type=int, default=3600,
                        help="Cache TTL in seconds")
    parser.add_argument("--force", action="store_true",
                        help="Force re-run all stages (ignore cache)")
    parser.add_argument("--preview-only", action="store_true",
                        help="Only render preview (skip full render)")
    parser.add_argument("--provider", default="gemini",
                        choices=["gemini", "deepseek", "volc"],
                        help="LLM provider for Plan stage")
    parser.add_argument("--version", action="version",
                        version=f"md-edit {__version__}")
    args = parser.parse_args()

    work_dir = os.path.abspath(args.work_dir)
    cache_dir = os.path.join(work_dir, "cache")
    cache = Cache(cache_dir, ttl=args.cache_ttl)

    if args.force:
        cache.clear_all()

    supervisor_prompt = ""
    if args.supervisor:
        spath = os.path.abspath(args.supervisor)
        if not os.path.isfile(spath):
            print(f"Error: supervisor file not found: {spath}", file=sys.stderr)
            sys.exit(1)
        with open(spath, "r", encoding="utf-8") as f:
            supervisor_prompt = f.read()

    print("=== Ingest ===")
    clips = ingest(args.input, work_dir)
    print(f"  Found {len(clips)} clips")

    print("=== Analyze ===")
    analyses = analyze_all(clips, cache, force=args.force)
    print(f"  Analyzed {len(analyses)} clips")

    print("=== Plan ===")
    plan = plan_episode(analyses, supervisor_prompt, cache, force=args.force, provider=args.provider)
    print(f"  Edit plan: {len(plan['edit_plan'])} segments")
    print(f"  Audit: {plan['audit']['overall_quality']}")

    if args.preview_only:
        print("=== Preview ===")
        preview_path = os.path.join(work_dir, "preview.mp4")
        render_preview(clips, plan["edit_plan"], preview_path)
        print(f"  Preview: {preview_path}")
        return

    print("=== Render ===")
    output_path = os.path.abspath(args.output)
    render_episode(clips, plan["edit_plan"], args.bgm, output_path, work_dir=work_dir)
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
