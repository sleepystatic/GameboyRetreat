# Email templates

Drafts for Resend Broadcasts. Replace anything in `[brackets]`. Keep them plain and
short — plain-text-looking email outperforms heavily designed email for a one-person
business, because it reads like a person wrote it.

Subject lines matter more than body copy. Two options are given for each so you can
A/B test them.

---

## 1. Launch broadcast — to everyone

Send once, to your whole list, when the site goes live.

**Subject A:** `The shop is live (and it's a Game Boy)`
**Subject B:** `You can now browse my restorations inside a working Game Boy`

**Preview text:** `Every console I've restored, in one place — plus something new.`

```
Hi [first name],

Quick note: gameboyretreat.com is live.

Every console I restore now goes up there — with proper photos, what I
actually did to it, and what's still available. If you've bought from me
on Mercari or Marketplace before, this is the same work, just without the
platform fees in the middle.

One thing I had fun with: the whole site runs inside a working Game Boy.
D-pad to browse, A to select. It works on your phone too — turn it sideways
and it becomes a GBA.

  [Browse the shop →] gameboyretreat.com/shop

Two new things I'm offering:

  MOD MY CONSOLE — Send me the Game Boy you already own and I'll do the
  same work to it. New shell, full clean, screen mods, any faults repaired.
  Cheaper than buying a restored one because you already have the hardware.

  ONE-TO-ONE LESSONS — An hour over Zoom, screen shared both ways. I walk
  you through a mod you want to learn on your own console.

Everything now carries a 30-day warranty. If something I touched fails in
that window, I put it right.

Thanks for reading — and for being an early customer.

[Your name]
Gameboy Retreat

You're getting this because you bought from me or signed up at
gameboyretreat.com. [Unsubscribe] any time.
```

---

## 2. Drip sequence — new subscribers

Three emails, automatic, after someone signs up. Resend Broadcasts has no automation
yet, so send these manually in batches until you move to a provider that does — or
schedule them.

### 2a. Immediately on signup

**Subject A:** `You're on the list`
**Subject B:** `Here's what to check before you buy a used Game Boy`

```
Hi,

You'll now hear from me first when a console is finished — usually a few
days before it goes public. Most are one of a kind, so that head start
matters.

While you're waiting, the five things I check on every unit that comes in.
Worth knowing before you buy a used handheld from anyone:

  1. Screen — look for vertical lines. Usually a reflowable ribbon cable,
     but factor in the repair cost.
  2. Battery terminals — green or white crust means corrosion. Cleanable,
     but it can eat traces underneath.
  3. Sound — the headphone jack and speaker fail independently. Test both.
  4. Buttons — mushy usually means dirty contacts, not broken ones.
  5. Cartridge slot — bent pins are the one repair that isn't worth it on
     a cheap unit.

Any questions about a console you already own, just reply. I read everything.

[Your name]
```

### 2b. Three days later

**Subject A:** `What "refurbished" actually means here`
**Subject B:** `From $15 and broken to like new`

```
Hi,

Most listings that say "refurbished" mean "wiped with a cloth."

Here's what it means when I say it:

  - Fully disassembled, board out of the shell
  - Board cleaned properly, corrosion treated at the terminals
  - New shell, new buttons, new membranes
  - Any fault actually repaired, not worked around
  - Reassembled and played through a real session before it's listed

That's why it takes me a few hours per console and why they last.

  [See what's finished right now →] gameboyretreat.com/shop

If you've got one sitting in a drawer that doesn't work, I can do this to
it rather than you buying another — that's the Mod My Console service.

[Your name]
```

### 2c. Seven days later

**Subject A:** `Want to learn to do this yourself?`
**Subject B:** `The mod most people should start with`

```
Hi,

If you've been reading these and thinking "I'd like to try this" — you can.

Most people should start with a shell swap. No soldering, about an hour,
and the result is dramatic. If you can follow a screwdriver, you can do it.

The one that trips people up is a backlight or IPS install, because it
involves ribbon cables that are easy to tear and a screen that has to sit
flush. That's the one worth having someone walk you through.

I do one-to-one sessions over Zoom — an hour, screen shared both ways, on
whatever console you want to work on.

  [Book a session →] gameboyretreat.com/shop

Or if you'd rather I just did it: send it in.

[Your name]
```

---

## 3. Segmented — past purchasers only

> **Only for people who bought on gameboyretreat.com.** Mercari, OfferUp and
> Facebook don't give you buyer emails, and using a marketplace buyer's name or
> address to market to them violates those platforms' terms. Past marketplace
> customers can't be emailed — they can only be reached through content they
> choose to follow.

Send only to people who have bought. Warmer tone, and the ask is different: this
audience is where reviews and repeat business come from.

**Subject A:** `How's your [console] holding up?`
**Subject B:** `A favour, if you have 30 seconds`

**Preview text:** `Plus something new I'm offering to previous customers first.`

```
Hi [first name],

You bought a [console] from me [timeframe] ago. Genuinely hoping it's
still going strong — if it isn't, reply and I'll sort it out.

Two things:

FIRST, a favour. Reviews are what let a one-person shop compete with
stores that have thousands. If the console has held up, 30 seconds saying
so helps more than you'd think.

  [Leave a review →] [your review link]

SECOND, something new. I'm now taking send-in work: your own console,
same treatment. New shell, full clean, screen mods, faults repaired. Since
you already know the standard of the work, previous customers get first
look — and [10]% off the first send-in.

  Use code [RETURNING10] at checkout.

  [Mod My Console →] gameboyretreat.com/shop

Everything now carries a 30-day warranty, including work on consoles you
already own.

Thanks for buying from a small shop.

[Your name]
Gameboy Retreat
```

---

## Sending notes

- **From:** everything sends as `support@gameboyretreat.com`, the shop's one
  public address. If the list grows into the thousands, give newsletters their
  own reputation: verify a subdomain such as `news.gameboyretreat.com` in Resend
  and send marketing from it, so a spam-complaint spike can't touch order
  confirmations.
- **Reply-to:** `support@gameboyretreat.com` - a real inbox you actually read.
  Replies to marketing email are the highest-intent messages you'll get.
- **Send Tuesday–Thursday, mid-morning** in your customers' timezone. Weekends
  underperform for retail.
- **Never buy or scrape a list.** One spam complaint rate spike and your domain
  reputation is damaged for months.
- **Warm up gradually.** If you're importing hundreds of past customers, send to a
  few dozen first, then scale over a week or two. A brand-new sending domain
  blasting 500 emails at once looks exactly like a spammer to inbox providers.
