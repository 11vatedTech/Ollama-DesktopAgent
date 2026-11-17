# Code signing options (G14)

Researched 2026-11-16 from public sources; prices and eligibility change, so confirm
on the provider's own page before buying. The project is public and Apache-2.0.

## What signing does and does not buy

- Signing replaces "Unknown publisher" with a verified name and makes tampering
  detectable. It does **not** remove SmartScreen warnings on day one: since 2024 EV
  certificates no longer get instant reputation, so OV, EV and cloud signing all build
  reputation through download history.
- Since June 2023, OV and EV private keys must live on certified hardware (a token or an
  HSM, including cloud HSMs). A plain PFX file from a public CA is no longer issued.

## Options

| Route | Cost | Publisher shown | Who can use it | Work needed here |
|---|---|---|---|---|
| **Azure Artifact Signing** (formerly Trusted Signing) | about $9.99/month (5 000 signatures) | your verified name | individuals in the **USA and Canada**; organisations also in the EU and UK | add `signtool` with the Artifact Signing dlib to `build_release.ps1` (today it signs with `Set-AuthenticodeSignature` from a thumbprint or PFX) |
| **SignPath Foundation** | free for OSI-licensed open source | "SignPath Foundation" (not you) | open-source projects it accepts; acceptance is discretionary and has been refused for projects without "broader recognition" | GitHub Actions integration; a manual approval per release |
| **Certum Open Source Code Signing** | roughly EUR 25-70 per year (sources differ) | "Open Source Developer, <your name>" | individuals with identity verification; open-source use only (revoked if used commercially) | cloud key (SimplySign); signing in CI needs an unofficial tool, locally the certificate appears in the Windows store and the current script can use its thumbprint |
| **OV certificate on a hardware token** (Sectigo, SSL.com, DigiCert and others) | typically a few hundred USD per year plus token | your verified name | anyone who passes validation | none: the token's certificate appears in `Cert:\CurrentUser\My`, so `build_release.ps1 -SigningThumbprint` works as is |
| EV certificate | about 30-40 % more than OV | verified organisation name | organisations | as OV; only needed for kernel drivers |

## Recommendation

1. If you are an individual in the USA or Canada: **Azure Artifact Signing**. It is the
   cheapest route that shows your own name, needs no hardware, and suits CI. The cost
   is engineering work: an Artifact Signing path in `build_release.ps1`.
2. Elsewhere, for an open-source release: **Certum Open Source**, signing locally with
   the existing thumbprint path. Apply to SignPath Foundation in parallel if a free route
   that shows "SignPath Foundation" as publisher is acceptable.
3. If the product will be sold: an **OV certificate** (Certum's open-source certificate
   would be revoked for commercial use).

Whichever you choose: sign every release with a timestamp (the script already uses
one), and expect SmartScreen warnings until downloads build reputation.

## Sources

- Microsoft Learn: [Code signing options for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
- [Azure Artifact Signing review and pricing (itechguides)](https://www.itechguides.com/best/code-signing-software/azure-artifact-signing/), [devclass, January 2026](https://www.devclass.com/security/2026/01/14/code-signing-windows-apps-may-be-easier-and-more-secure-with-new-azure-artifact-service/4079554), [Melatonin setup notes](https://melatonin.dev/blog/code-signing-on-windows-with-azure-trusted-signing/)
- [Open source code signing certificates in 2026 (my-ssl)](https://my-ssl.com/learn/open-source-code-signing-certificate); SignPath and Certum experience reports in public GitHub issues, e.g. [opc-hub #496](https://github.com/Jde-cpp/opc-hub/pull/496), [hece #49](https://github.com/altintasutku/hece/issues/49)
- [EV vs OV code signing (my-ssl)](https://my-ssl.com/learn/ev-vs-ov-code-signing), [EV code signing token vs cloud (OneSign)](https://onesign.sg/blog/industry-news/ev-code-signing-token-vs-cloud-2026)
