// App entry — assembles all sections

const App = () => (
  <div className="min-h-screen bg-paper text-ink-900">
    <Header />
    <main>
      <Hero />
      <LogoStrip />
      <ProblemSolution />
      <HowItWorks />
      <Benefits />
      <ChannelsTabs />
      <LiveDemoChat />
      <Testimonial />
      <Pricing />
      <FAQ />
      <AboutUs />
      <CTABook />
    </main>
    <Footer />
  </div>
);

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
