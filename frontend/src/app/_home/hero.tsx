import { ArrowRight, BellRing, ScanEye, ShieldCheck } from "lucide-react";
import Link from "next/link";

import "./home.css";

// Our Junction 2026 submission video, from YouTube's privacy-enhanced domain, which sets no
// cookies until it is played. The title is the video's own on YouTube.
const VIDEO = {
  src: "https://www.youtube-nocookie.com/embed/UQtvAowfapA",
  title: "Recall/Remind - AGB",
};

/** The home page: the product name and pitch, our submission video, and the way to try it. */
export function Hero() {
  return (
    <section className="home" aria-labelledby="home-title">
      <div className="home-text">
        <div className="home-lead">
          <h1 id="home-title">
            Recall<span className="wordmark-accent">/Remind</span>
          </h1>
          <p className="home-pitch">
            Give your Meta glasses a private visual memory and real-time notifications
            triggered from visual cues.
          </p>
          <Link className="home-cta" href="/try">
            Try it out <ArrowRight aria-hidden="true" />
          </Link>
        </div>
        <div className="home-more">
          <ul className="home-caps">
            <li>
              <ScanEye aria-hidden="true" />
              <p>
                <strong>Recall</strong> Ask about anything you&apos;ve seen, and get the answer
                with the visual evidence.
              </p>
            </li>
            <li>
              <BellRing aria-hidden="true" />
              <p>
                <strong>Remind</strong> Tell it what to watch for, and it notifies you when it
                appears.
              </p>
            </li>
          </ul>
          <p className="home-trust">
            <ShieldCheck aria-hidden="true" />
            Faces blurred before processing · self-hosted models, no third parties
          </p>
        </div>
      </div>

      <iframe
        className="home-video"
        src={VIDEO.src}
        title={VIDEO.title}
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        // YouTube refuses to play an embed whose page sends no referrer.
        referrerPolicy="strict-origin-when-cross-origin"
        allowFullScreen
      />
    </section>
  );
}
