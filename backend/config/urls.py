from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView
from django.http import Http404
from django.shortcuts import render

# ============================================================
# CONTACT PAGE VIEW
# ============================================================
# A bare TemplateView only accepts GET, so the contact form's
# POST was returning HTTP 405. This view handles both.

def contact_view(request):
    if request.method == "POST":
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip()
        topic = request.POST.get("topic", "").strip()
        message = request.POST.get("message", "").strip()
        # TODO: send email / save to a Lead model — stub for now
        return render(request, "marketing/talk_to_us.html", {"submitted": True})
    return render(request, "marketing/talk_to_us.html")


# ============================================================
# INDUSTRY DETAIL DATA (Section 06+07 — dynamic per-slug pages)
# ============================================================
# Doc (Section 07) mein sirf Real Estate ka poora example diya gaya hai.
# Baaki 6 industries ka capabilities list + example conversation isi
# structure/tone ko follow karke banaya gaya hai (industries.html ke
# card copy ko expand karke) — doc ka literal text nahi hai.
# Agar in industries ke liye specific copy ho, yahan direct replace karo.

INDUSTRIES = {
    'real-estate': {
        'name': 'Real Estate',
        'role': 'AI Property Sales Employee',
        'icon': 'RE',
        'icon_class': 'ind-card__icon--1',
        'seo_title': 'AI Employee for Real Estate | AI Real Estate Assistant | LiftBot',
        'meta_description': "Use an AI employee for real estate to handle property enquiries, qualify leads, collect buyer requirements and support your sales team.",
        'hero_line1': "Property buyers don't always enquire during office hours.",
        'hero_line2': "LiftBot can help your real estate business respond, understand requirements and capture potential opportunities around the clock.",
        'capabilities': [
            'Property enquiries', 'Buyer requirements', 'Budget questions',
            'Location preferences', 'Property type', 'Lead capture',
            'Initial qualification', 'Viewing enquiries',
        ],
        'example_customer': "I'm looking for a 2-bedroom apartment in Dubai under AED 1.5M.",
        'example_bot': "I can help with that. Which areas are you considering, and are you looking for a ready property or an off-plan development?",
        'cta_text': 'Build Your AI Sales Employee →',
    },
    'travel': {
        'name': 'Travel & Tourism',
        'role': 'AI Travel Consultant',
        'icon': 'TT',
        'icon_class': 'ind-card__icon--2',
        'seo_title': 'AI Employee for Travel & Tourism | AI Travel Consultant | LiftBot',
        'meta_description': "Use an AI employee for travel and tourism to answer trip questions, understand traveller requirements and move enquiries forward.",
        'hero_line1': "Travellers don't always ask questions during office hours.",
        'hero_line2': "LiftBot can help your travel business respond, understand trip requirements and move enquiries forward around the clock.",
        'capabilities': [
            'Trip enquiries', 'Destination questions', 'Travel dates',
            'Budget range', 'Group size', 'Package interest',
            'Lead capture', 'Initial qualification',
        ],
        'example_customer': "I'm looking for a 5-day trip to Bali for two people in December.",
        'example_bot': "I can help with that. Are you looking for a package with flights and hotel included, or just accommodation?",
        'cta_text': 'Build Your AI Travel Employee →',
    },
    'ecommerce': {
        'name': 'E-commerce',
        'role': 'AI Shopping Assistant',
        'icon': 'EC',
        'icon_class': 'ind-card__icon--3',
        'seo_title': 'AI Employee for E-commerce | AI Shopping Assistant | LiftBot',
        'meta_description': "Use an AI employee for e-commerce to help customers discover products, answer questions and provide first-level support.",
        'hero_line1': "Shoppers don't always browse during business hours.",
        'hero_line2': "LiftBot can help your store respond, guide product discovery and support customers around the clock.",
        'capabilities': [
            'Product enquiries', 'Sizing & fit questions', 'Order status',
            'Return & exchange questions', 'Product recommendations', 'Stock availability',
            'Lead capture', 'First-level support',
        ],
        'example_customer': "Do you have this jacket in a medium, and does it run true to size?",
        'example_bot': "Yes, medium is in stock. It fits true to size — most customers keep their usual size.",
        'cta_text': 'Build Your AI Shopping Employee →',
    },
    'education': {
        'name': 'Education',
        'role': 'AI Admissions Assistant',
        'icon': 'ED',
        'icon_class': 'ind-card__icon--4',
        'seo_title': 'AI Employee for Education | AI Admissions Assistant | LiftBot',
        'meta_description': "Use an AI employee for education to answer course questions, collect prospective student information and support admissions.",
        'hero_line1': "Prospective students don't always ask questions during office hours.",
        'hero_line2': "LiftBot can help your institution respond, answer course questions and support admissions around the clock.",
        'capabilities': [
            'Course enquiries', 'Eligibility questions', 'Fee & scholarship questions',
            'Application process', 'Intake dates', 'Prospective student capture',
            'Initial qualification', 'Admission enquiries',
        ],
        'example_customer': "What are the eligibility requirements for the MBA program starting in January?",
        'example_bot': "For the January MBA intake, you'll need a bachelor's degree and at least two years of work experience. Would you like details on the application process?",
        'cta_text': 'Build Your AI Admissions Employee →',
    },
    'healthcare': {
        'name': 'Healthcare',
        'role': 'AI Reception Assistant',
        'icon': 'HC',
        'icon_class': 'ind-card__icon--5',
        'seo_title': 'AI Employee for Healthcare | AI Reception Assistant | LiftBot',
        'meta_description': "Use an AI employee for healthcare to handle general enquiries and support approved appointment-related conversations.",
        'hero_line1': "Patients don't always call during clinic hours.",
        'hero_line2': "LiftBot can help your practice handle general enquiries and support approved appointment-related conversations around the clock.",
        'capabilities': [
            'General enquiries', 'Clinic hours & location', 'Service questions',
            'Approved appointment conversations', 'Insurance questions', 'Pre-visit information',
            'Lead capture', 'First-level support',
        ],
        'example_customer': "What are your clinic hours on weekends, and do you accept walk-ins?",
        'example_bot': "We're open Saturdays 9am–2pm. Walk-ins are welcome, though booking ahead helps reduce your wait time.",
        'cta_text': 'Build Your AI Reception Employee →',
    },
    'professional-services': {
        'name': 'Professional Services',
        'role': 'AI Client Intake Employee',
        'icon': 'PS',
        'icon_class': 'ind-card__icon--6',
        'seo_title': 'AI Employee for Professional Services | AI Client Intake Employee | LiftBot',
        'meta_description': "Use an AI employee for professional services to understand client requirements, capture enquiries and connect prospects with your team.",
        'hero_line1': "Potential clients don't always reach out during office hours.",
        'hero_line2': "LiftBot can help your firm understand requirements, capture enquiries and connect prospects with your team around the clock.",
        'capabilities': [
            'Service enquiries', 'Client requirements', 'Budget questions',
            'Scope questions', 'Consultation requests', 'Lead capture',
            'Initial qualification', 'Prospect handoff',
        ],
        'example_customer': "I need help with a contract review — how does your pricing work?",
        'example_bot': "Happy to help. Contract reviews are typically scoped per project. Could you share a bit more about the contract type so I can connect you with the right person?",
        'cta_text': 'Build Your AI Intake Employee →',
    },
    'hospitality': {
        'name': 'Hospitality',
        'role': 'AI Guest Assistant',
        'icon': 'HO',
        'icon_class': 'ind-card__icon--7',
        'seo_title': 'AI Employee for Hospitality | AI Guest Assistant | LiftBot',
        'meta_description': "Use an AI employee for hospitality to answer common guest questions and help visitors find the information they need.",
        'hero_line1': "Guests don't always have questions during front-desk hours.",
        'hero_line2': "LiftBot can help your property answer common questions and help visitors find the information they need around the clock.",
        'capabilities': [
            'Booking enquiries', 'Amenity questions', 'Check-in & check-out times',
            'Local recommendations', 'Special requests', 'Availability questions',
            'Lead capture', 'First-level support',
        ],
        'example_customer': "What time is check-in, and do you have a late check-out option?",
        'example_bot': "Check-in is from 3pm. Late check-out until 1pm is available on request, subject to availability.",
        'cta_text': 'Build Your AI Guest Employee →',
    },
}


def industry_detail(request, slug):
    industry = INDUSTRIES.get(slug)
    if not industry:
        raise Http404("Industry not found")
    return render(
        request,
        'marketing/individual_industry_ex.html',
        {'industry': industry, 'slug': slug},
    )


# ============================================================

urlpatterns = [
    path('admin/', admin.site.urls),

    # --- Marketing Pages (Professional Setup) ---
    path('', TemplateView.as_view(template_name='marketing/home.html'), name='home'),
    path('about/', TemplateView.as_view(template_name='marketing/about.html'), name='about'),
    path('contact/', contact_view, name='contact'),
    path('ai-employees/', TemplateView.as_view(template_name='marketing/ai_employees.html'), name='ai_employees'),
    path('features/', TemplateView.as_view(template_name='marketing/features.html'), name='features'),
    path('how-it-works/', TemplateView.as_view(template_name='marketing/how_it_works.html'), name='how_it_works'),
    path('solutions/', TemplateView.as_view(template_name='marketing/solutions.html'), name='solutions'),

    path('industries/', TemplateView.as_view(template_name='marketing/industries.html'), name='industries'),
    path('industries/<slug:slug>/', industry_detail, name='industry_detail'),

    path('use-cases/', TemplateView.as_view(template_name='marketing/usecases.html'), name='use_cases'),
    path('demo/', TemplateView.as_view(template_name='marketing/demo.html'), name='demo'),
    path('pricing/', TemplateView.as_view(template_name='marketing/pricing.html'), name='pricing'),
    path('customers/', TemplateView.as_view(template_name='marketing/customers.html'), name='customers'),
    path('resources/', TemplateView.as_view(template_name='marketing/resources.html'), name='resources'),
    path('blog/', TemplateView.as_view(template_name='marketing/blog.html'), name='blog'),
    path('early-access/', TemplateView.as_view(template_name='marketing/early_access.html'), name='early_access'),
    path('faq/', TemplateView.as_view(template_name='marketing/faq.html'), name='faq'),
    path('guide/', TemplateView.as_view(template_name='marketing/liftbot_guide.html'), name='guide'),
    path('support/', TemplateView.as_view(template_name='marketing/support.html'), name='support'),
    path('security/', TemplateView.as_view(template_name='marketing/security.html'), name='security'),

    path('privacy/', TemplateView.as_view(template_name='marketing/privacy.html'), name='privacy'),
    path('terms/', TemplateView.as_view(template_name='marketing/terms.html'), name='terms'),
    path('cookies/', TemplateView.as_view(template_name='marketing/cookies.html'), name='cookies'),
    # ---------------------------------------------

    # --- Existing App Routes ---
    path('', include('apps.accounts.urls')),
    path('', include('apps.workspaces.urls')),
    path('employees/', include('apps.employees.urls')),
    path('knowledge/', include('apps.knowledge.urls')),
    path('chat/', include('apps.chat.urls')),
    path('leads/', include('apps.leads.urls')),
    path('billing/', include('apps.billing.urls')),
    path('api/widget/', include('apps.chat.widget_urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)