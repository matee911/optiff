# Contributing

Thanks for looking. Bug reports, failing sample files and pull requests are all
welcome.

Before a pull request can be merged, you need to agree to the Contributor
Licence Agreement below. There is a reason for it, and it is spelled out rather
than hidden: this project is open source **and** the basis of a commercial
product. That combination only works if one party holds the rights to the whole
engine.

## What you need to run

The project pins its interpreter and its checks:

```bash
pip install -e ".[dev]"
pre-commit install     # ruff format, ruff check, pyrefly on every commit
pytest                 # ~2 s, needs no sample files
```

The suite generates its own files. Every case met in a real document has a
recipe in `tests/sample_files.py`; nothing binary lives in the repository.
If you hit a file this tool gets wrong, the most useful thing you can send is
**a new case in that catalogue** reproducing the shape of the problem, not the
file itself.

## Rules the code follows

- **The original is never modified.** Results are written alongside, under a new
  name.
- **Every write is verified.** SHA256 of each channel's pixels before and after;
  on a mismatch the output is deleted rather than kept.
- **Only channel data and its size fields change.** Everything else in the
  structure is copied byte for byte.

A change that cannot keep those three is not a change to this project.

## Contributor Licence Agreement

By submitting a contribution you agree to the following.

### 1. Definitions

"The project owner" means matee911, the copyright holder of this project.
"You" means the copyright owner, or the person legally authorised by the
copyright owner, who is entering this agreement. "Contribution" means any work
of authorship you deliberately submit to this project, in any form and through
any channel, including code, documentation and test material.

### 2. Copyright licence

You grant the project owner a perpetual, worldwide, non-exclusive, no-charge,
royalty-free and irrevocable copyright licence to reproduce, prepare derivative
works of, publicly display, publicly perform, sublicense and distribute your
contribution and such derivative works.

**The right to sublicense is the point of this document.** It is what allows the
same engine to be released under the GNU GPL v3 and, separately, under
commercial terms. Without it, a single contribution would permanently prevent
that.

### 3. Patent licence

You grant the project owner and every recipient of the software a perpetual,
worldwide, non-exclusive, no-charge, royalty-free and irrevocable patent licence
to make, use, offer to sell, sell, import and otherwise transfer the work. This
covers only those patent claims you own or control that are necessarily
infringed by your contribution alone, or by the combination of your contribution
with this project.

If you start patent litigation alleging that this project, or a contribution
within it, infringes a patent, the licences granted to you under this agreement
end on the day the action is filed.

### 4. What you are stating

- The contribution is your original work, or you have the right to submit it
  under this agreement.
- If your employer has rights to work you produce, you have permission to make
  the contribution, or your employer has waived those rights.
- The contribution does not knowingly infringe anyone's rights.

If any part of your contribution is **not** your original work, say so in the
pull request: identify the source, the author and the licence it arrives under.
It may still be usable, but it has to be labelled.

### 5. What you keep

You keep the copyright in your contribution. This is a licence, not an
assignment. You may use your own work anywhere else, in any way, with no
restriction from this agreement.

### 6. What the project promises back

Every contribution accepted here will remain available under the GNU General
Public License v3. A commercial licence may exist alongside it, but it will
never replace the open one, and your work will not disappear behind a paywall.

### 7. No warranty, no obligation

You provide your contribution "as is", without warranties of any kind. Nothing
here obliges the project to merge, keep or ship any contribution.

## How to sign

Include this line in the description of your pull request:

```
I have read CONTRIBUTING.md and I agree to the Contributor Licence Agreement.
```

That is the whole ceremony. One line per contributor, not per pull request.

---

*This agreement follows the shape of the widely used Apache Individual
Contributor License Agreement, adapted for a single project owner and for the
right to sublicense. It has not been reviewed by a lawyer. If you are
contributing on behalf of a company, or the contribution is substantial, get
your own advice first.*
