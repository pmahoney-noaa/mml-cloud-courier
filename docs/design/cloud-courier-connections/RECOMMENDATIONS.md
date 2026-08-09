# UX recommendations — connection management

Ranked. Cost key: **S** = a few hours · **M** = a day or two · **L** = a week-ish, or touches the
service contract.

Items 9 and 10 propose rewording two of the four contractual strings. They are proposals only —
the designs in this package use the current wording verbatim.

---

## 1. A credential that cannot upload is currently saved without complaint — **M**

*Not a visual issue. The most consequential thing found while designing this flow.*

`create_profile` rejects only when list or read fails:

```python
if not (result.can_list and result.can_read):
    raise HTTPException(status_code=400, detail=result.summary())
```

But `PreflightResult.ok_for(UPLOAD)` requires all five capabilities — list, read, write, compose,
delete — and its own docstring says why: *"Requiring everything at creation beats discovering a
gap overnight."* Creation does not require everything. A read-only key therefore saves cleanly,
looks healthy in the manager, and fails on the first upload — which is exactly the overnight
discovery the preflight was written to prevent.

Three ways to close it, in order of preference:

1. **Reject at creation** unless `ok_for(UPLOAD)` passes, with an opt-in for download-only
   profiles. Strictest, matches the docstring's intent, but changes the create contract.
2. **Save it and label it.** Store the capability set, and show a `DOWNLOAD ONLY` pill on the
   manager card plus a warning at the moment the connection is chosen for an upload. No contract
   change; the gap becomes visible instead of silent.
3. **At minimum**, show the mixed summary on the success screen rather than the generic one, so
   the user reads *"can list and read but cannot write, compose and delete"* at the moment of
   creation instead of never.

Option 2 is what I would ship. Option 3 is an hour's work and should happen regardless.

---

## 2. Route the delete refusal somewhere instead of stopping — **S**

`ProfileInUse` produces `profile 4 is used by 7 job(s) and cannot be deleted while they exist`.
Two problems: it says `profile 4` to a user who knows it as "Leg 2 imagery (2025)", and it ends
the conversation.

Show it inline on the card in plain words, and add `Show those 7 jobs`, which closes the dialog
and filters the main window's rail to that profile. The user's actual goal is almost always
tidying up old jobs; this is the only screen that knows which ones.

---

## 3. Give the health gate a way out — **S**

The gate is correct — nothing credential-shaped should be reachable while the service is down.
But `COPY_SERVICE_FIRST` tells the user to start the service from the main window, and this
dialog may be covering it, with no obvious next move except closing everything.

Add `Check again` and `Open the main window` beside the message. Keep the two credential cards
visible and readable while disabled, so the wait is spent learning what the two options are.

---

## 4. Show the preflight as five probes, not one sentence — **S**

*"Validating the connection against the bucket…"* can sit unchanged for fifteen seconds against a
cold bucket, which is indistinguishable from a hang. The five probes already run in sequence;
listing them and ticking them off costs one signal from the service — or, if that is too invasive,
a client-side sequence of the five names advancing on a timer while the single call is pending.

The names also teach the user why write, compose and delete are needed, which is the exact
knowledge they will want if the next screen is a permissions failure.

---

## 5. Name the auth types in words users have — **S**

The manager currently prints `[service_account_key]`, `[oauth_user]`, `[adc]`. Show
`SERVICE ACCOUNT KEY`, `GOOGLE SIGN-IN`, `COMMAND-LINE CREDENTIALS` — and for the legacy `adc`
case add the sentence that actually matters: *"Created outside this app. It works, but only this
machine's signed-in account can use it."*

A research scientist should never have to look up what Application Default Credentials are to
understand a row in a list.

---

## 6. Make the 7-day expiry visible after creation, not only during it — **S**

The disclosure is prominent at the moment of choice, which is where the gate finding required it.
But the consequence arrives weeks later, when a transfer fails at 2am.

On any `oauth_user` connection whose last check is older than seven days, turn the last-check line
amber and say so: *"Checked Aug 2 — this sign-in may have expired. Check it before the next
transfer."* Same colors, no new copy contract, and it is the only recurring reminder the user
will ever get.

---

## 7. Explain a connection once, at the top of the manager — **S**

There is no definition of "connection" anywhere in the product. Scientists infer it from context
and often assume it means a network connection.

One sentence in the manager header: *"Each connection is a bucket and a credential the service
keeps and uses on its own. Transfers pick one by name."* That sentence also quietly explains why
the credential is stored — which is the question the key-deletion notice raises later.

---

## 8. Point the wrong-file-type error at the right card — **S**

`load_key_file` already names the actual type, which is good and rare. Follow it with the
recovery: *"That file is an OAuth client configuration, not a key. Use it under Google sign-in
below, or ask your administrator for a service-account key."*

A user holding `client_secret_884213.json` is one card away from succeeding. Nothing currently
tells them that.

---

## 9. Copy proposal: split `COPY_CHOOSE_SIGNIN` into a claim and a caveat — **S**, needs a test change

Current (89 words, one paragraph, four clauses joined by dashes):

> Google sign-in — good for interactive or short-lived use. Transfers keep running after you sign
> out, but the sign-in itself can expire and need repeating — for apps registered in Google's
> 'testing' status it stops working after about 7 days. For a connection that must run unattended
> for months, prefer a service account key.

Proposed, same four facts, three sentences, the expiry first:

> Google sign-in — good for interactive or short-lived use. The sign-in itself can expire and need
> repeating: for apps registered in Google's 'testing' status it stops working after about 7 days.
> Transfers already running keep going after you sign out. For a connection that must run
> unattended for months, prefer a service account key.

The substance is unchanged and nothing is softened. The reason to consider it: in the current
order, the reassuring clause ("transfers keep running") arrives before the warning, and readers
who stop at the first dash leave with the opposite of the intended impression. The design in this
package compensates with a `CAN EXPIRE IN ~7 DAYS` pill; if that pill ships, this rewording is
optional.

---

## 10. Copy proposal: make `COPY_DELETE_ORIGINAL` specific — **S**, needs a test change

Current:

> The service now holds an encrypted copy of this key. You may delete the original file.

Proposed:

> The service now holds an encrypted copy of this key. You may delete the original file:
> C:\Users\…\mml-courier-key.json

Same sentence, with the path appended. "The original file" is an abstraction at the exact moment
the user has three similar JSON files in Downloads and cannot remember which one they picked.

If the test assertion is a substring or prefix match, this may need no test change at all. The
design in this package achieves most of the benefit without touching the string — the path is
printed underneath in mono — so treat this as the lower-value of the two proposals.

---

## 11. Disable Next rather than validating after the fact — **S**

Today, missing Name or Bucket surfaces as *"Name and bucket are required."* only after the user
has clicked a credential button and possibly opened a file dialog. Requiredness should be knowable
before the click: keep `Next: credential` disabled until both fields are non-empty.

Same for the duplicate-name 409 — surface it under the Name field on return, not as a message box.

---

## 12. Alignment and structure notes — **S** each

- **One filled button per region.** `New connection` in the manager; the recommended credential
  path on step 2; the step's forward action in the footer. Nothing else is filled.
- **Per-card actions, not a selection-driven button bar.** The manager's current
  `New / Check / Remove / Close` row makes Check and Remove act on whatever is selected, which is
  ambiguous the moment more than one card is on screen. Move Check and Remove onto their cards and
  leave only Close in the footer.
- **The refusal renders inside the card**, not as a `QMessageBox`. A modal over a modal hides the
  thing it is talking about.
- **Step 2 has no footer primary.** The two card buttons are the actions; a Next would imply a
  third path that does not exist.
- **The step rail is three single words.** Position indicator, not documentation.
- **Helper text below the field, never as placeholder text.** Placeholders vanish exactly when
  the user starts typing and needs them.
- **Recovery buttons sit with the message that caused them**, not in the footer — the footer holds
  navigation only.

---

## 13. Deliberately not recommended

- **No "test connection" step before Create.** The create call already validates; adding a
  separate test button means two round trips and a state where the two disagree.
- **No credential-type auto-detection from the file.** `load_key_file` naming the actual type is
  better than silently accepting the wrong file and routing it elsewhere; the user should learn
  which file they handed over.
- **No storing the OAuth client config in the app.** It stays an env var or a browsed file, as
  today. Bundling it would make every install share one client and inherit its testing-status
  expiry.
- **No fifth color family for "success".** Verification success uses `accent`. Green would create
  a fourth semantic color for one screen and weaken the rule that red means failure.
