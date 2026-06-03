document.addEventListener('DOMContentLoaded', function () {
    const canvas = document.getElementById('subdivisionChart');
    if (!canvas) return;

    const labels = JSON.parse(canvas.dataset.labels || '[]');
    const data   = JSON.parse(canvas.dataset.chartdata || '[]');

    new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: canvas.dataset.label || 'Population',
                data: data,
                backgroundColor: 'rgba(0,122,94,0.7)',
                borderColor: 'rgba(0,122,94,1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            scales: { y: { beginAtZero: true } }
        }
    });
});
