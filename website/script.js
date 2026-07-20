// Smooth Scroll & Navigation Active State
document.addEventListener('DOMContentLoaded', function() {
    // Add scroll-based navigation styling
    const nav = document.querySelector('.nav');
    
    window.addEventListener('scroll', function() {
        if (window.scrollY > 50) {
            nav.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.08)';
        } else {
            nav.style.boxShadow = 'none';
        }
    });

    // Intersection Observer for fade-in animations
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };

    const observer = new IntersectionObserver(function(entries) {
        entries.forEach(function(entry) {
            if (entry.isIntersecting) {
                entry.target.classList.add('fade-in');
                observer.unobserve(entry.target);
            }
        });
    }, observerOptions);

    // Observe all major sections and cards
    const elementsToAnimate = document.querySelectorAll(`
        .section-header,
        .feature-card,
        .feature-card-large,
        .problem-item,
        .solution-layer,
        .arch-layer,
        .module-detail-card,
        .metric-card,
        .benefit-item,
        .step-card,
        .project-stats
    `);

    elementsToAnimate.forEach(function(el, index) {
        el.style.animationDelay = (index * 0.05) + 's';
        observer.observe(el);
    });

    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(function(anchor) {
        anchor.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            if (targetId === '#') return;
            
            const targetElement = document.querySelector(targetId);
            if (targetElement) {
                e.preventDefault();
                const navHeight = document.querySelector('.nav').offsetHeight;
                const targetPosition = targetElement.offsetTop - navHeight - 20;
                
                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });
            }
        });
    });

    // Active nav link highlighting
    const sections = document.querySelectorAll('section[id]');
    
    window.addEventListener('scroll', function() {
        let current = '';
        const scrollPosition = window.scrollY + 100;
        
        sections.forEach(function(section) {
            const sectionTop = section.offsetTop;
            const sectionHeight = section.offsetHeight;
            
            if (scrollPosition >= sectionTop && scrollPosition < sectionTop + sectionHeight) {
                current = section.getAttribute('id');
            }
        });
        
        document.querySelectorAll('.nav-links a').forEach(function(link) {
            link.style.color = '';
            if (link.getAttribute('href') === '#' + current) {
                link.style.color = '#6366f1';
            }
        });
    });

    // Add parallax effect to hero section
    const hero = document.querySelector('.hero');
    if (hero) {
        window.addEventListener('scroll', function() {
            const scrollY = window.scrollY;
            if (scrollY < 500) {
                hero.style.backgroundPositionY = scrollY * 0.5 + 'px';
            }
        });
    }

    // Code window hover effect
    const codeWindows = document.querySelectorAll('.code-window');
    codeWindows.forEach(function(window) {
        window.addEventListener('mouseenter', function() {
            this.style.transform = 'perspective(1000px) rotateY(0deg) rotateX(0deg) scale(1.02)';
        });
        
        window.addEventListener('mouseleave', function() {
            this.style.transform = 'perspective(1000px) rotateY(-5deg) rotateX(2deg)';
        });
    });

    // Button ripple effect
    document.querySelectorAll('.btn').forEach(function(button) {
        button.addEventListener('click', function(e) {
            const ripple = document.createElement('span');
            const rect = this.getBoundingClientRect();
            const size = Math.max(rect.width, rect.height);
            const x = e.clientX - rect.left - size / 2;
            const y = e.clientY - rect.top - size / 2;
            
            ripple.style.cssText = `
                position: absolute;
                width: ${size}px;
                height: ${size}px;
                left: ${x}px;
                top: ${y}px;
                background: rgba(255, 255, 255, 0.3);
                border-radius: 50%;
                transform: scale(0);
                animation: ripple 0.6s ease-out;
                pointer-events: none;
            `;
            
            this.style.position = 'relative';
            this.style.overflow = 'hidden';
            this.appendChild(ripple);
            
            setTimeout(function() {
                ripple.remove();
            }, 600);
        });
    });

    // Add CSS for ripple animation
    const style = document.createElement('style');
    style.textContent = `
        @keyframes ripple {
            to {
                transform: scale(2);
                opacity: 0;
            }
        }
    `;
    document.head.appendChild(style);

    // Animate numbers on scroll into view
    const numberObserver = new IntersectionObserver(function(entries) {
        entries.forEach(function(entry) {
            if (entry.isIntersecting) {
                animateNumbers(entry.target);
                numberObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.5 });

    document.querySelectorAll('.stat-number, .metric-value, .metric-value-small').forEach(function(el) {
        numberObserver.observe(el);
    });

    function animateNumbers(element) {
        const text = element.textContent;
        const numberMatch = text.match(/[\d.]+/);
        
        if (numberMatch) {
            const target = parseFloat(numberMatch[0]);
            const isFloat = numberMatch[0].includes('.');
            const suffix = text.replace(numberMatch[0], '');
            
            let current = 0;
            const duration = 1500;
            const startTime = performance.now();
            
            function update(currentTime) {
                const elapsed = currentTime - startTime;
                const progress = Math.min(elapsed / duration, 1);
                const easeOut = 1 - Math.pow(1 - progress, 3);
                
                current = target * easeOut;
                
                if (isFloat) {
                    element.textContent = current.toFixed(3) + suffix;
                } else {
                    element.textContent = Math.round(current) + suffix;
                }
                
                if (progress < 1) {
                    requestAnimationFrame(update);
                } else {
                    element.textContent = text;
                }
            }
            
            requestAnimationFrame(update);
        }
    }

    // Console welcome message
    console.log('%cWT Automation', 'font-size: 24px; font-weight: bold; color: #6366f1;');
    console.log('%c面向 WT 仿真软件的桌面自动化平台', 'font-size: 14px; color: #475569;');
    console.log('%c━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━', 'color: #e2e8f0;');
    console.log('%c• 46 步完整流程 · 5/5 运行成功', 'color: #10b981;');
    console.log('%c• 平均耗时 119.597s · 极差 1.137s', 'color: #3b82f6;');
    console.log('%c• 结构化主执行 + AI 兜底策略', 'color: #8b5cf6;');
});