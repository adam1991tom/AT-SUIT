# AT SUITE Data Processing Agreement

It is written to meet UK GDPR Article 28. Words in [square brackets] are still to be filled in.

This agreement is between the customer (the **controller**) and [COMPANY NAME] Ltd (the **processor**, "we"). It forms part of the AT SUITE [Terms of Service](/legal/terms) and applies whenever we process personal data for the customer through the hosted service.

## 1. What we will do

1.1 We will process the customer's personal data only on its documented instructions. These terms, the customer's settings in AT SUITE and its admins' actions in the app count as instructions. We will tell the customer if we think an instruction breaks data protection law.

1.2 Everyone we let process the data is bound to keep it confidential.

1.3 We will keep the data secure with the measures in Annex 2, and review them as risks and technology change.

1.4 We will help the customer, as far as we reasonably can, to answer requests from people exercising their data rights. Most can be handled in the app, for example by deleting an account under People.

1.5 We will help the customer with security, breach notification, impact assessments and consultation with the ICO, taking into account what we know about the processing.

## 2. Sub-processors

2.1 The customer agrees to the sub-processors in Annex 3.

2.2 We will give at least [30] days' notice by email before adding or replacing a sub-processor. The customer can object on reasonable data protection grounds. If we can't resolve the objection, the customer may end the affected plan and get a refund of prepaid fees for the unused period.

2.3 We will put the same data protection obligations as this agreement on each sub-processor in a written contract, and remain responsible for them.

## 3. Transfers outside the UK

We will only transfer personal data outside the UK where UK law allows it. That means to a country with adequacy regulations (such as the EEA), under the UK Extension to the EU-US Data Privacy Framework, or under the ICO's International Data Transfer Agreement or Addendum.

## 4. Personal data breaches

4.1 We will tell the customer without undue delay, and aim to within [24] hours, after becoming aware of a personal data breach affecting its data.

4.2 We will give the customer what it needs to assess the breach and tell the ICO and the people affected where required. This includes what happened, the data and people affected, likely effects and what we're doing about it.

## 5. At the end

When the customer's plan ends, we will keep its data for [30] days so it can renew or export it, then delete it, unless the law requires us to keep it. Backups are overwritten on their normal cycle, within [5] weeks. On request we will confirm deletion in writing.

## 6. Audits

We will make available the information needed to show we meet this agreement, including our Cyber Essentials certificate and answers to reasonable security questionnaires. Where that isn't enough, we will allow an audit by the customer or an auditor it appoints, on reasonable notice, no more than once a year, under a duty of confidentiality, and at the customer's cost.

## 7. Liability

Each party's liability under this agreement is subject to the limits in the Terms of Service. [Solicitor: consider a separate cap for data protection claims.]

## Annex 1: Details of the processing

| Item | Details |
|---|---|
| Subject matter and duration | Providing the AT SUITE hosted service for as long as the customer's plan lasts, plus the deletion period in clause 5 |
| Nature and purpose | Hosting, storing, syncing and displaying data so the customer's crew can run live events: accounts and sign-in, crew chat and help requests, handover notes, show reports, the laptop list and status, licence checks, the help library and the AI helper |
| People whose data is processed | The customer's staff and freelance technicians; presenters and speakers whose details the customer adds; anyone named in chat, notes or reports |
| Types of personal data | Names, usernames, roles, password hashes, work contact details, chat and help messages, handover notes, show reports, presenter names, emails and phone numbers, audit log entries (sign-ins, IP addresses, changes), laptop names and status |
| Special category data | Not intended. It could appear by chance in chat, notes or reports, and the customer should tell its users not to add it |
| Data that does **not** reach us | Live audio, which is processed on the laptop. Laptop screen snapshots, which are held only in server memory while a manager views them, never saved, and off by default. [From version 1.10.0: caption text and transcripts, which stay on the laptop.] |

## Annex 2: Security measures

- Each venue runs in its own sealed container with its own data volume and private network. Containers are read-only, don't run as root, and have memory and CPU limits.
- The server only accepts web traffic through Cloudflare, encrypted end to end (TLS, "Full (strict)").
- A new venue can only be claimed with its one-time setup code.
- Server sign-in is by SSH key only. A firewall allows only the ports needed, and security updates install automatically.
- Passwords are stored as hashes. New accounts must change the default password at first sign-in. Every sign-in, failed sign-in, reset and settings change is logged.
- Transcripts and handover notes are encrypted at rest. Transcripts are deleted automatically after 12 hours by default (1 to 168 hours, set by the customer) and are never backed up.
- Backups are encrypted, run nightly to a Hetzner Storage Box in the same EU region, and kept for 7 daily and 4 weekly copies.
- Only [adam / named staff] can reach the servers, using two-factor sign-in for every provider account.
- [Cyber Essentials certificate number and date, once certified.]

## Annex 3: Sub-processors

| Sub-processor | What it does | Where |
|---|---|---|
| Hetzner Online GmbH | Cloud servers and encrypted backups | [Germany or Finland] (EEA) |
| Cloudflare, Inc. | Network security, encryption and delivery of traffic to the hosted service | United States and global network (UK Extension to the EU-US Data Privacy Framework, with ICO addendum fallback) |
| [Email provider, if service emails carry customer personal data] | [Sending account and alert emails] | [Location] |
