<script>
	import { base } from '$app/paths';

	let { data } = $props();
</script>

<h1>Podcast feeds</h1>

{#if data.podcasts.length === 0}
	<p>No feeds yet.</p>
{:else}
	<ul>
		{#each data.podcasts as podcast}
			<li><a href="{base}/feeds/{podcast.name}.xml">{podcast.display_name}</a></li>
		{/each}
	</ul>
{/if}
