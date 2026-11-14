import { ArrowDown, ArrowUpRight, Asterisk, Code2, MapPin } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { io } from "next/cache";
import { Suspense } from "react";

import { api } from "@/lib/api/server";

import { ApiKeyJoke } from "./api-key-joke";
import { NoteForm } from "./note-form";
import { RefreshNotes } from "./refresh-notes";

const team = [
  { name: "Ilmari", initials: "IL", handle: "ilmari99" },
  { name: "Arno", initials: "AR", handle: "arnotoro" },
  { name: "Leevi", initials: "LE", handle: "LeeviKamarainen" },
  { name: "Pauli", initials: "PA", handle: "PauliAnt" },
];

export default function Home() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="site-shell">
        <header className="site-header">
          <a className="wordmark" href="#" aria-label="Junction 2026 home">
            <Asterisk
              aria-hidden="true"
              className="brand-symbol"
              strokeWidth={2.5}
            />
            JUNCTION<span className="wordmark-year">/26</span>
          </a>
          <nav aria-label="Main navigation">
            <Link href="/memory">Memory lab</Link>
            <a href="#workspace">Workspace</a>
            <a href="#crew">The crew</a>
            <a
              className="source-link"
              href="https://github.com/ilmari99/junction2026"
            >
              <Code2 size={16} aria-hidden="true" />
              <span>Source code</span>
              <ArrowUpRight size={14} aria-hidden="true" />
            </a>
          </nav>
        </header>

        <main id="main">
          <section className="hero" aria-labelledby="hero-title">
            <div className="hero-copy">
              <p className="eyebrow">
                <span className="small-cross" aria-hidden="true">
                  +
                </span>{" "}
                FOUR PEOPLE. INFINITE WHAT IFS.
              </p>
              <h1 id="hero-title">
                Build something
                <br />
                worth staying
                <br />
                <span>up for.</span>
              </h1>
              <p className="hero-description">
                Big ideas, open tabs, and just enough chaos.
                <br className="desktop-break" /> Our little corner of Junction
                2026.
              </p>
              <div className="hero-actions">
                <a className="primary-link" href="#workspace">
                  Enter the workspace <ArrowDown size={18} aria-hidden="true" />
                </a>
                <span className="hero-note">
                  Less talking.
                  <br />
                  More making.
                </span>
              </div>
            </div>
            <figure className="hero-art">
              <div className="art-topline">
                <span>EXPERIMENT NO. 001</span>
                <Asterisk size={18} aria-hidden="true" />
              </div>
              <Image
                src="/images/junction-chrome.webp"
                alt="A sculptural chrome asterisk catching warm orange light in a dark studio"
                width={1024}
                height={1024}
                sizes="(max-width: 700px) 100vw, (max-width: 1100px) 48vw, 600px"
                preload
                className="chrome-art"
              />
              <figcaption>
                <span>
                  DIFFERENT DIRECTIONS.
                  <br />
                  ONE JUNCTION.
                </span>
                <span className="art-caption-detail">
                  AI-GENERATED
                  <br />
                  HUMAN-CURATED
                </span>
              </figcaption>
            </figure>
          </section>

          <section className="event-strip" aria-label="Event details">
            <div>
              <span className="strip-label">THE WHEN</span>
              <p>
                13—15 November <span>2026</span>
              </p>
            </div>
            <div>
              <span className="strip-label">THE WHERE</span>
              <p>
                <MapPin size={15} aria-hidden="true" /> Espoo, Finland
              </p>
            </div>
            <div>
              <span className="strip-label">THE MINDSET</span>
              <p>Make it. Break it. Make it better.</p>
            </div>
            <Asterisk className="strip-asterisk" size={34} aria-hidden="true" />
          </section>

          <section
            className="workspace-section"
            id="workspace"
            aria-labelledby="workspace-title"
          >
            <div className="section-heading">
              <div>
                <p className="eyebrow">01 / SHARED HEADSPACE</p>
                <h2 id="workspace-title">
                  The scratchpad<span>.</span>
                </h2>
              </div>
              <p>
                Half-formed ideas welcome.
                <br />
                That’s how the good ones start.
              </p>
            </div>
            <div className="workspace-grid">
              <Suspense fallback={<NotesLoading />}>
                <NotesBoard />
              </Suspense>
              <ApiKeyJoke />
            </div>
          </section>

          <section
            className="crew-section"
            id="crew"
            aria-labelledby="crew-title"
          >
            <div className="crew-intro">
              <p className="eyebrow">02 / THE USUAL SUSPECTS</p>
              <h2 id="crew-title">
                Good company.
                <br />
                Questionable sleep schedules.
              </h2>
            </div>
            <ul className="crew-list">
              {team.map((person, index) => (
                <li key={person.handle}>
                  <a
                    href={`https://github.com/${person.handle}`}
                    aria-label={`${person.name} on GitHub`}
                  >
                    <span
                      className={`crew-avatar avatar-${index}`}
                      aria-hidden="true"
                    >
                      {person.initials}
                    </span>
                    <span>
                      {person.name}
                      <small>@{person.handle}</small>
                    </span>
                    <ArrowUpRight size={17} aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          </section>
        </main>

        <footer className="site-footer">
          <a className="footer-brand" href="#">
            <Asterisk size={22} aria-hidden="true" /> JUNCTION / 2026
          </a>
          <p>Built with curiosity. And probably another coffee.</p>
          <a href="https://2026.hackjunction.com/">
            See you at Junction <ArrowUpRight size={14} aria-hidden="true" />
          </a>
        </footer>
      </div>
    </>
  );
}

async function NotesBoard() {
  // The client constructs request metadata before fetch; keep that out of the static shell.
  await io();
  let notes;
  try {
    const { data } = await api.GET("/api/notes", {
      signal: AbortSignal.timeout(5000),
      cache: "no-store",
    });
    notes = data;
  } catch {
    // The workspace still renders when the API is temporarily unavailable.
  }

  if (!notes) {
    return (
      <div className="notes-panel notes-unavailable" role="status">
        <Asterisk size={32} aria-hidden="true" />
        <h3>The scratchpad is taking a breather.</h3>
        <p>We couldn’t load the team notes. Give it another try.</p>
        <RefreshNotes />
      </div>
    );
  }

  return (
    <div className="notes-panel">
      <div className="panel-heading">
        <h3>
          Team notes <span className="note-count">{notes.length}</span>
        </h3>
        <span className="shared-label">
          <span /> SHARED WITH THE CREW
        </span>
      </div>
      {notes.length ? (
        <ol
          className="notes-list"
          tabIndex={0}
          aria-label="Team notes, newest first"
        >
          {notes.toReversed().map((note, index) => (
            <li key={note.id}>
              <span className="note-index">
                {String(index + 1).padStart(2, "0")}
              </span>
              <p>{note.text}</p>
              <Asterisk size={15} aria-hidden="true" />
            </li>
          ))}
        </ol>
      ) : (
        <div className="notes-empty">
          <Asterisk size={26} aria-hidden="true" />
          <p>A blank page. A very good place to start.</p>
          <span>Leave the first thought below.</span>
        </div>
      )}
      <NoteForm />
    </div>
  );
}

function NotesLoading() {
  return (
    <div className="notes-panel notes-loading" role="status">
      <span className="eyebrow">OPENING THE SCRATCHPAD</span>
      <div />
      <div />
      <p>Gathering the team’s thoughts…</p>
    </div>
  );
}
