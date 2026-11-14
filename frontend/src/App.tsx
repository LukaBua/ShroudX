import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, Orbit, Crosshair, Telescope, Users, ExternalLink, Menu, X } from 'lucide-react';
import Galaxy from './Galaxy';
import Explorer from './Explorer';
import { team } from './team';

export default function App() {
  const hero = useRef<HTMLElement>(null);
  const [menu, setMenu] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        const progress = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : Math.min(scrollY / Math.max(innerHeight, 1), 1);
        hero.current?.style.setProperty('--progress', String(progress)); frame = 0;
      });
    };
    addEventListener('scroll', onScroll, { passive: true }); onScroll();
    const params = new URLSearchParams(location.search);
    if (params.has('target') || params.has('ra')) requestAnimationFrame(() => document.getElementById('explorer')?.scrollIntoView({ behavior: 'instant' }));
    return () => { removeEventListener('scroll', onScroll); cancelAnimationFrame(frame); };
  }, []);
  const jump = () => setMenu(false);
  return <div className={expanded ? 'site focus-mode' : 'site'}>
    <a className="skip-link" href="#explorer">Skip to explorer</a>
    <header className="navigation">
      <a className="wordmark" href="#home" aria-label="SPHEREx home"><Orbit size={27} strokeWidth={1.3} /><span>SPHERE<span className="brand-x">x</span></span><span className="brand-divider" /><span className="brand-caption">A UNIVERSE<br />IN MOTION</span></a>
      <button className="mobile-menu icon-button" aria-label={menu ? 'Close menu' : 'Open menu'} aria-expanded={menu} onClick={() => setMenu(!menu)}>{menu ? <X /> : <Menu />}</button>
      <nav className={menu ? 'nav-links open' : 'nav-links'} aria-label="Main navigation"><a href="#explorer" onClick={jump}>The explorer</a><a href="#about" onClick={jump}>The mission</a><a href="#team" onClick={jump}>Our team</a><a className="nav-cta" href="#explorer" onClick={jump}>Enter the sky <ArrowUpRight size={15} /></a></nav>
    </header>
    <main>
      <section id="home" className="hero" ref={hero}>
        <div className="galaxy-stage"><Galaxy /></div>
        <div className="hero-coordinate mono">ILLUSTRATIVE SKY / NOT OBSERVATIONAL DATA</div>
        <div className="hero-copy"><p className="eyebrow"><span className="status-dot" /> AN OPEN WINDOW INTO THE INFRARED</p><h1>The sky is<br />never <em>still.</em></h1><p className="hero-description">A universe of quiet movement.<br />Explore SPHEREx observations and see<br className="desktop-break" /> what changes between moments.</p><a className="button button-light" href="#explorer">Explore the sky <ArrowUpRight size={18} /></a></div>
        <div className="galaxy-caption mono"><span className="tiny-cross">+</span> A DIFFERENT PERSPECTIVE<br /><span className="caption-offset">ON A FAMILIAR SKY</span></div>
        <div className="hero-bottom"><a href="#about"><span className="scroll-ring"><ArrowDown size={16} /></span><span className="mono">SCROLL TO DISCOVER</span></a><span className="mono">NASA SPACE APPS PROJECT <span className="muted">/</span> 2026</span><span className="mono hero-counter">01 — 03</span></div>
      </section>
      <section id="about" className="mission-strip"><div className="section-index mono">01 / THE BIGGER PICTURE</div><div className="mission-copy"><h2>A new way to see<br /><em>what’s out there.</em></h2><p>SPHEREx surveys the sky in 102 colours of infrared light. We bring its observations together so you can look closer, compare dates, and explore a universe that keeps moving.</p></div><div className="mission-facts"><div><span>102</span><p>infrared colours</p></div><div><span>6</span><p>detector arrays</p></div><div><Orbit size={31} strokeWidth={1} /><p>one changing sky</p></div></div><a className="text-link" href="https://science.nasa.gov/mission/spherex/" target="_blank" rel="noreferrer">Meet the mission <ExternalLink size={13} /></a></section>
      <section id="explorer" className="explorer-section"><div className="section-heading"><div><p className="eyebrow">02 / THE OBSERVATION ROOM</p><h2>Your window. <em>Your discovery.</em></h2></div><p>Choose a patch of sky.<br />Let the observations tell their story.</p></div><Explorer expanded={expanded} onExpand={() => setExpanded(!expanded)} /><div className="explorer-footnote"><span><Crosshair size={14} /> Observations, not interpolated moments.</span><a href="/legacy" target="_blank" rel="noreferrer">Open original dashboard <ArrowUpRight size={14} /></a></div></section>
      <section className="method-section"><p className="eyebrow">A LITTLE CONTEXT GOES A LONG WAY</p><div className="method-grid"><article><span className="method-number">01</span><Telescope size={22} /><h3>Find your field.</h3><p>Search an object by name, or use coordinates to explore a region of sky.</p></article><article><span className="method-number">02</span><Orbit size={22} /><h3>Look through time.</h3><p>Blink between observations, combine frames, or inspect differences between periods.</p></article><article><span className="method-number">03</span><Crosshair size={22} /><h3>Stay curious. Check.</h3><p>Read the dates, wavelengths, and warnings. A visible change is a starting point, not proof of movement.</p></article></div></section>
      <section id="team" className="team-section"><div className="section-heading"><div><p className="eyebrow">03 / THE PEOPLE BEHIND THE PIXELS</p><h2>Different perspectives.<br /><em>One shared curiosity.</em></h2></div><p>A team brought together by<br />a sky full of questions.</p></div><p className="team-note"><Users size={14} /> Meet the team. Bios and role assignments below are sample introductions.</p><div className="team-grid">{team.map(member => <article className="team-card" key={member.initials}><div className="portrait">{member.photo ? <img src={member.photo} alt={`${member.name} — ${member.role}`} loading="lazy" style={{ objectPosition: member.photoPosition }} /> : <><div className="portrait-orbit" /><div className="portrait-silhouette" /><span className="portrait-placeholder mono">PORTRAIT / {member.initials}</span></>}<span className="portrait-plus">+</span></div><p className="eyebrow team-role">{member.role}</p><h3>{member.name}</h3><p>{member.description}</p></article>)}</div></section>
      <section className="closing"><Orbit size={40} strokeWidth={1} /><h2>There’s always<br /><em>more to see.</em></h2><a href="#explorer" className="button button-light">Back to the explorer <ArrowRight size={17} /></a></section>
    </main>
    <footer><a href="#home" className="wordmark"><Orbit size={21} /><span>SPHERE<span className="brand-x">x</span></span></a><p>An independent NASA Space Apps project.<br />SPHEREx data: NASA / JPL-Caltech / IPAC.</p><a href="https://irsa.ipac.caltech.edu/Missions/spherex.html" target="_blank" rel="noreferrer">Data & credits <ArrowUpRight size={14} /></a><span className="mono">BUILT WITH CURIOSITY, 2026</span></footer>
  </div>;
}
